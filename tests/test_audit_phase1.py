"""Regression checks for the phase 1 audit; all data is synthetic and temporary."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import streamlit as st
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from streamlit.testing.v1 import AppTest

from config import ROOT, Settings, get_settings
from database.database import build_engine, init_db, session_factory
from database.models import AgentRun, CandidateProfile
from database.repositories import save_profile
from services.profile_service import read_profile


@pytest.fixture
def app_database(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "app.db"}')
    get_settings.cache_clear()
    st.cache_resource.clear()
    engine = build_engine()
    init_db(engine)
    yield engine
    engine.dispose()
    st.cache_resource.clear()
    get_settings.cache_clear()


def test_real_example_preserved():
    path = ROOT / 'examples/profile.md'
    name, content = read_profile(path.name, path.read_bytes())
    assert name == 'profile.md'
    assert content == path.read_text()
    assert read_profile(r'C:\private\profile.md', b'  # Profile\n')[0] == 'profile.md'


def test_size_configuration():
    for size in [0, -1]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, max_profile_bytes=size)


def test_relative_database_independent_of_cwd(tmp_path, monkeypatch):
    import database.database as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.chdir('/')
    engine = build_engine('sqlite:///nested/profile.db')
    init_db(engine)
    assert (tmp_path / 'nested/profile.db').is_file()
    engine.dispose()


def test_init_idempotent_and_integrity(app_database):
    init_db(app_database)
    init_db(app_database)
    with app_database.connect() as connection:
        assert connection.scalar(text('PRAGMA integrity_check')) == 'ok'
        assert connection.scalar(text('PRAGMA foreign_keys')) == 1
        assert connection.execute(text('PRAGMA foreign_key_check')).all() == []


def test_concurrent_profile_deduplication(app_database):
    factory = session_factory(app_database)
    def import_one(index):
        with factory.begin() as session:
            profile, created = save_profile(session, f'{index}.md', '# Shared profile')
            return profile.id, created
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(import_one, range(8)))
    assert len({profile_id for profile_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_sql_errors_hide_bound_profile_content(app_database):
    sensitive = 'SYNTHETIC_PRIVATE_PROFILE'
    with pytest.raises(OperationalError) as caught, app_database.connect() as connection:
        connection.execute(text('SELECT * FROM nonexistent WHERE content = :content'), {'content': sensitive})
    assert sensitive not in str(caught.value)


def test_streamlit_upload_save_duplicate_and_navigation(app_database):
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    content = (ROOT / 'examples/profile.md').read_bytes()
    app.file_uploader[0].set_value(('profile.md', content, 'text/markdown')).run()
    assert not app.exception
    app.button[0].click().run()
    assert app.success[0].value == 'Profil enregistré.'
    first_id = app.session_state['profile_id']
    app.button[0].click().run()
    assert any('déjà enregistré' in element.value for element in app.info)
    with session_factory(app_database)() as session:
        assert session.scalar(select(func.count()).select_from(CandidateProfile)) == 1
    app.file_uploader[0].set_value(('second.md', b'# Second', 'text/markdown')).run()
    app.button[0].click().run()
    assert app.session_state['profile_id'] != first_id
    app.selectbox[0].set_value(first_id).run()
    app.sidebar.radio[0].set_value('État du projet').run()
    app.sidebar.radio[0].set_value('Profil candidat').run()
    assert not app.exception
    assert app.session_state['profile_id'] == first_id
    assert app.selectbox[0].value == first_id


def test_streamlit_invalid_upload(app_database):
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    app.file_uploader[0].set_value(('profile.md', b'\xff', 'text/markdown')).run()
    assert not app.exception
    assert 'UTF-8' in app.error[0].value
    assert len(app.button) == 0


def test_streamlit_storage_failure_redacted(app_database, monkeypatch, caplog):
    import ui.candidate as candidate
    def failing_save(*args, **kwargs):
        raise OperationalError('INSERT', {'profile': 'SYNTHETIC_PRIVATE_PROFILE'}, Exception('failure'))
    monkeypatch.setattr(candidate, 'save_profile', failing_save)
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    app.file_uploader[0].set_value(('profile.md', b'# Profile', 'text/markdown')).run()
    app.button[0].click().run()
    assert not app.exception
    assert "Impossible d'enregistrer" in app.error[0].value
    assert 'SYNTHETIC_PRIVATE_PROFILE' not in caplog.text
    with session_factory(app_database)() as session:
        assert session.scalar(select(func.count()).select_from(CandidateProfile)) == 0


def test_streamlit_read_failure(app_database, monkeypatch):
    import ui.candidate as candidate
    def failing_list(*args):
        raise OperationalError('SELECT', {}, Exception('failure'))
    monkeypatch.setattr(candidate, 'list_profiles', failing_list)
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    assert not app.exception
    assert 'Impossible de lire' in app.error[0].value


def test_streamlit_invalid_configuration(app_database, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'not a database URL')
    get_settings.cache_clear()
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    assert not app.exception
    assert "Impossible d'initialiser" in app.error[0].value
