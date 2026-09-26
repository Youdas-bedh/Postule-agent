import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_script_shell_syntax():
    subprocess.run(['bash', '-n', str(ROOT / 'scripts/start_vllm.sh')], check=True)


def test_start_script_passes_config_without_loading_models(tmp_path):
    fake_vllm = tmp_path / 'vllm'
    fake_vllm.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    fake_vllm.chmod(0o755)
    env_file = tmp_path / '.env'
    env_file.write_text('MODEL_NAME=synthetic/test-model\nGPU_MEMORY_UTILIZATION=0.80\nMAX_MODEL_LEN=8192\nVLLM_PORT=8123\n')
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/start_vllm.sh')],
        env={**os.environ, 'POSTULE_ENV_FILE': str(env_file), 'VLLM_BIN': str(fake_vllm)},
        text=True, capture_output=True, check=True,
    )
    assert 'synthetic/test-model' in result.stdout
    assert '--max-model-len\n8192' in result.stdout
    assert '--gpu-memory-utilization\n0.80' in result.stdout
    assert '--language-model-only' in result.stdout
    assert '--reasoning-parser\nqwen3' in result.stdout


def test_start_script_fails_cleanly_for_missing_model(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('LLM_PROVIDER=vllm\n')
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/start_vllm.sh')],
        env={**os.environ, 'POSTULE_ENV_FILE': str(env_file)}, text=True, capture_output=True,
    )
    assert result.returncode == 2
    assert 'MODEL_NAME doit être défini' in result.stderr
