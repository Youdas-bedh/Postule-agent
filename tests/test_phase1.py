from pathlib import Path
import pytest
from sqlalchemy import inspect, select, func
from sqlalchemy.exc import IntegrityError
from streamlit.testing.v1 import AppTest
from database.database import build_engine, init_db, session_factory
from database.models import AgentRun, Application, CandidateProfile
from database.repositories import save_profile, list_profiles
from services.profile_service import read_profile


def test_markdown_validation():
    assert read_profile('../profile.md', b'\xef\xbb\xbf# Profil')[0] == 'profile.md'
    assert read_profile('profile.md', b'\xef\xbb\xbf# Profil')[1] == '# Profil'
    for name, content in [('cv.pdf', b'test'), ('profile.md', b'  '), ('profile.md', b'\xff'), ('profile.md', b'x' * 1_048_577)]:
        with pytest.raises(ValueError):
            read_profile(name, content)


def test_storage_persistence_and_deduplication(tmp_path):
    url = f'sqlite:///{tmp_path / "test.db"}'
    engine = build_engine(url)
    init_db(engine)
    assert set(inspect(engine).get_table_names()) == {'companies', 'contacts', 'job_offers', 'applications', 'emails', 'candidate_profiles', 'cv_files', 'agent_runs', 'application_events'}
    factory = session_factory(engine)
    with factory.begin() as session:
        profile, created = save_profile(session, 'profile.md', '# Profil')
        assert created
        first_id = profile.id
    with factory.begin() as session:
        profile, created = save_profile(session, 'renamed.md', '# Profil')
        assert not created and profile.id == first_id
    engine.dispose()
    engine = build_engine(url)
    with session_factory(engine)() as session:
        assert list_profiles(session)[0].content == '# Profil'
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
    engine.dispose()


def test_foreign_keys_and_rollback(tmp_path):
    engine = build_engine(f'sqlite:///{tmp_path / "test.db"}')
    init_db(engine)
    factory = session_factory(engine)
    with pytest.raises(IntegrityError), factory.begin() as session:
        save_profile(session, 'profile.md', '# Profil')
        session.add(Application(company_id=999, profile_id=999, application_type='SPONTANEOUS'))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateProfile)) == 0


def test_streamlit_empty_and_saved_profile(tmp_path, monkeypatch):
    from config import get_settings
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "ui.db"}')
    get_settings.cache_clear()
    import streamlit as st
    st.cache_resource.clear()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
    assert not app.exception
    engine = build_engine()
    with session_factory(engine).begin() as session:
        save_profile(session, 'profile.md', '# Profil enregistré')
    app.run()
    assert not app.exception
    assert app.text_area[0].value == '# Profil enregistré'
    app.sidebar.radio[0].set_value('État du projet').run()
    assert not app.exception
    st.cache_resource.clear()
    get_settings.cache_clear()
