from unittest.mock import Mock

import pytest
import requests
from pydantic import ValidationError

from config import Settings
from llm.base import LLMConfigurationError, LLMResponseError, LLMUnavailableError
from llm.factory import create_provider
from llm.ollama_provider import OllamaProvider
from llm.openai_provider import OpenAIProvider
from llm.vllm_provider import LocalVLLMProvider


def configured_settings(**values):
    return Settings(_env_file=None, model_name='test/model', **values)


def test_defaults_and_environment_fields():
    settings = configured_settings()
    assert settings.llm_provider == 'vllm'
    assert settings.gpu_memory_utilization == .85
    assert settings.max_model_len == 16384


def test_reject_invalid_settings():
    for values in [
        {'llm_provider': 'unknown'}, {'llm_timeout': 0}, {'llm_temperature': 3},
        {'llm_max_tokens': 0}, {'gpu_memory_utilization': 1.0},
        {'max_model_len': 0}, {'vllm_base_url': 'not-a-url'},
        {'vllm_base_url': 'http://localhost:not-a-port/v1'},
        {'vllm_base_url': 'http://user:password@localhost:8000/v1'},
    ]:
        with pytest.raises(ValidationError):
            configured_settings(**values)


def test_factory_uses_selected_provider():
    assert isinstance(create_provider(configured_settings(llm_provider='vllm')), LocalVLLMProvider)
    assert isinstance(create_provider(configured_settings(llm_provider='ollama')), OllamaProvider)
    assert isinstance(create_provider(configured_settings(llm_provider='openai')), OpenAIProvider)


def test_vllm_health_and_generation_contract():
    client = Mock()
    client.request_json.side_effect = [
        {'data': [{'id': 'test/model'}]},
        {'choices': [{'message': {'content': ' Bonjour. '}}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 3}},
    ]
    provider = LocalVLLMProvider(configured_settings(), client=client)
    status = provider.check_connection()
    assert status.available and status.model == 'test/model'
    assert provider.generate('Dis bonjour', system_prompt='En français') == 'Bonjour.'
    assert provider.last_usage == {'prompt_tokens': 12, 'completion_tokens': 3}
    assert client.request_json.call_args_list[1].kwargs['json'] == {
        'model': 'test/model',
        'messages': [{'role': 'system', 'content': 'En français'}, {'role': 'user', 'content': 'Dis bonjour'}],
        'temperature': .2, 'max_tokens': 512,
        'chat_template_kwargs': {'enable_thinking': False},
    }


def test_vllm_structured_generation_uses_json_mode_and_budget_override():
    client = Mock()
    client.request_json.return_value = {'choices': [{'message': {'content': '{"answer":"ok"}'}}]}
    provider = LocalVLLMProvider(configured_settings(), client=client)
    assert provider.generate('Return JSON', max_tokens=1024, json_mode=True) == '{"answer":"ok"}'
    payload = client.request_json.call_args.kwargs['json']
    assert payload['max_tokens'] == 1024
    assert payload['response_format'] == {'type': 'json_object'}


def test_vllm_missing_model_and_malformed_response():
    client = Mock()
    client.request_json.side_effect = [{'data': [{'id': 'other'}]}, {'choices': []}]
    provider = LocalVLLMProvider(configured_settings(), client=client)
    assert not provider.check_connection().available
    with pytest.raises(LLMResponseError):
        provider.generate('test')
    with pytest.raises(LLMConfigurationError):
        provider.generate('  ')


def test_vllm_unavailable_health_is_clean():
    client = Mock()
    client.request_json.side_effect = LLMUnavailableError('serveur arrêté')
    status = LocalVLLMProvider(configured_settings(), client=client).check_connection()
    assert not status.available and 'serveur arrêté' in status.message


def test_ollama_native_payload():
    client = Mock()
    client.request_json.side_effect = [
        {'models': [{'name': 'test/model'}]}, {'message': {'content': ' Bonjour '}}
    ]
    provider = OllamaProvider(configured_settings(llm_provider='ollama'), client=client)
    assert provider.check_connection().available
    assert provider.generate('Salut') == 'Bonjour'
    method, url = client.request_json.call_args.args
    assert (method, url) == ('POST', 'http://127.0.0.1:11434/api/chat')
    assert client.request_json.call_args.kwargs['json']['stream'] is False


def test_openai_provider_is_disabled_without_network_calls():
    provider = OpenAIProvider(configured_settings(llm_provider='openai'))
    assert not provider.check_connection().available
    with pytest.raises(LLMConfigurationError, match='phase future'):
        provider.generate('test')


def test_retry_then_success(monkeypatch):
    from llm.http_provider import RetryingHTTPClient
    response = Mock()
    response.status_code = 200
    response.json.return_value = {'ok': True}
    session = Mock()
    session.request.side_effect = [requests.Timeout('timeout'), response]
    monkeypatch.setattr('llm.http_provider.time.sleep', lambda _: None)
    client = RetryingHTTPClient('http://localhost:8000/v1', 2, session)
    assert client.request_json('GET', 'http://localhost:8000/v1/models') == {'ok': True}
    assert session.request.call_count == 2
    assert session.request.call_args.kwargs['timeout'] == 2


def test_retry_exhaustion_is_actionable_and_hides_url_credentials(monkeypatch):
    from llm.http_provider import RetryingHTTPClient
    session = Mock()
    session.request.side_effect = requests.ConnectionError('offline')
    monkeypatch.setattr('llm.http_provider.time.sleep', lambda _: None)
    client = RetryingHTTPClient('http://user:secret@localhost:8000/v1', 1, session)
    with pytest.raises(LLMUnavailableError) as error:
        client.request_json('GET', 'http://localhost:8000/v1/models')
    assert session.request.call_count == 3
    assert 'secret' not in str(error.value)


def test_http_error_and_bad_json_are_validated():
    from llm.http_provider import RetryingHTTPClient
    response = Mock()
    response.status_code = 404
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    session = Mock()
    session.request.return_value = response
    with pytest.raises(LLMUnavailableError, match='HTTP 404'):
        RetryingHTTPClient('http://localhost:8000/v1', 1, session).request_json('GET', 'x')
    response.status_code = 200
    response.raise_for_status.side_effect = None
    response.json.side_effect = ValueError('bad json')
    with pytest.raises(LLMResponseError, match='JSON invalide'):
        RetryingHTTPClient('http://localhost:8000/v1', 1, session).request_json('GET', 'x')


def test_streamlit_llm_status_with_mock(monkeypatch, tmp_path):
    import streamlit as st
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from config import get_settings
    from llm.base import ProviderStatus
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "status.db"}')
    monkeypatch.setenv('MODEL_NAME', 'test/model')
    get_settings.cache_clear()
    st.cache_resource.clear()
    provider = Mock()
    provider.name = 'vllm'
    provider.model_name = 'test/model'
    provider.base_url = 'http://127.0.0.1:8000/v1'
    provider.check_connection.return_value = ProviderStatus('vllm', 'test/model', provider.base_url, True, 'Serveur prêt.', .02)
    provider.generate.return_value = 'Test réussi.'
    monkeypatch.setattr('ui.llm_status.create_provider', lambda _: provider)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
    app.sidebar.radio[0].set_value('LLM Status').run()
    assert not app.exception
    assert app.metric[0].value == 'vllm'
    app.button[0].click().run()
    assert any('Serveur prêt' in element.value for element in app.success)
    app.button[1].click().run()
    assert any('Test réussi.' in element.value for element in app.main.markdown)
    get_settings.cache_clear()
    st.cache_resource.clear()
