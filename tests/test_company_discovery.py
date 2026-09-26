import json
from datetime import datetime, timezone
from unittest.mock import Mock
import httpx
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from agents.company_analysis import CompanyAnalysis, CompanyAnalysisAgent, Evidence, calculate_relevance, extract_fallback_evidence, ground_analysis_in_evidence, parse_analysis, validate_evidence_sources
from agents.company_discovery import CompanyDiscoveryAgent, _verified_official
from database.database import init_db
from database.models import Base, Company
from discovery.base import CompanyCandidate
from scrapers.website import WebsiteResult, clean_html, select_relevant_pages
from utils.company_names import normalize_company_name
from utils.urls import is_public_web_domain, is_third_party_domain, normalize_domain, normalize_url


@pytest.fixture
def session():
    engine = create_engine('sqlite:///:memory:')
    init_db(engine)
    with Session(engine) as db:
        yield db


def test_url_normalization_and_domain():
    assert normalize_domain('https://WWW.Example.com/fr/') == 'example.com'
    assert normalize_url('http://www.example.com/fr/?utm_source=x&fbclid=abc') == 'https://example.com/fr'
    assert normalize_url('example.com') == 'https://example.com/'
    assert normalize_url('javascript:alert(1)') is None
    assert normalize_url('https://user:pass@example.com') is None
    assert is_third_party_domain('https://fr.linkedin.com/company/x')
    assert not is_public_web_domain('127.0.0.1')


def test_company_name_normalization_is_exact_and_conservative():
    assert normalize_company_name('Société Étoile, S.A.S.') == 'societe etoile'
    assert normalize_company_name('Example Incorporated') == 'example'
    assert normalize_company_name('Example Labs') != normalize_company_name('Example AI')


def test_html_cleaning_and_page_selection():
    html = '''<html><head><title>Acme</title><meta name="description" content="Cloud tools"></head><body><nav>Menu</nav><main><h1>About us</h1><p>Acme builds reliable machine learning tools for scientific teams.</p><a href="/about-us">About</a><a href="https://other.test/ai">External</a><script>secret()</script></main><footer>Cookie banner</footer></body></html>'''
    page = clean_html(html, 'https://acme.test/')
    assert page.title == 'Acme' and 'machine learning' in page.text
    assert 'Menu' not in page.text and 'secret' not in page.text and 'Cookie banner' not in page.text
    selected = select_relevant_pages('https://acme.test/', html)
    assert selected and all(normalize_domain(url) == 'acme.test' for url in selected)
    assert any(url.endswith('/about-us') for url in selected)


def test_structured_analysis_validation_and_malformed_output():
    valid = {'company_name':'Acme', 'summary':'Builds tools.', 'evidence':[{'claim':'Builds tools', 'url':'https://acme.test/about', 'excerpt':'We build tools.'}], 'analysis_confidence':0.8}
    assert parse_analysis('```json\n' + json.dumps(valid) + '\n```').company_name == 'Acme'
    normalized = parse_analysis('{"company_name":"Acme","engineering_relevance":["data platform","cloud"],"technologies":"Python"}')
    assert normalized.engineering_relevance == 'data platform; cloud' and normalized.technologies == ['Python']
    omitted_quotes = parse_analysis('{"company_name":"Acme","evidence":[{"claim":"A claim","source_url":"https://acme.test/"}]}')
    assert omitted_quotes.evidence == []
    with pytest.raises(ValueError): parse_analysis('not json')
    with pytest.raises(ValueError): parse_analysis('{"company_name":"", "analysis_confidence": 2}')


def test_evidence_must_quote_a_fetched_same_domain_page():
    from scrapers.website import PageContent
    scrape = WebsiteResult('https://acme.test/', [PageContent('https://acme.test/about', text='Acme develops machine learning products.')])
    entries = [
        Evidence(claim='Develops ML', url='https://acme.test/about/', excerpt='Acme develops machine learning products.'),
        Evidence(claim='Unsupported', url='https://acme.test/missing', excerpt='Acme produces rockets.'),
        Evidence(claim='External', url='https://third.test/about', excerpt='Acme develops machine learning products.'),
    ]
    assert [e.claim for e in validate_evidence_sources(entries, scrape, 'acme.test')] == ['Develops ML']


def test_grounding_drops_unsubstantiated_analysis_details():
    analysis = CompanyAnalysis(
        company_name='Acme', summary='Acme offers many services worldwide.',
        main_activities=['Builds AI coding tools', 'Manufactures cars'],
        ai_data_relevance='The company leads sovereign cloud infrastructure.',
        evidence=[Evidence(claim='Coding tool', url='https://acme.test/', excerpt='AI-assisted coding and testing tools.')],
        analysis_confidence=.95,
    )
    grounded = ground_analysis_in_evidence(analysis)
    assert grounded.main_activities == ['Builds AI coding tools']
    assert grounded.ai_data_relevance and 'IA/Data' in grounded.ai_data_relevance
    assert grounded.analysis_confidence == .5


def test_extractive_fallback_returns_verbatim_site_sentences():
    from scrapers.website import PageContent
    sentence = 'OVHcloud offers public and private cloud computing services for businesses worldwide.'
    scrape = WebsiteResult('https://ovhcloud.com/', [PageContent('https://ovhcloud.com/', text=sentence)])
    result = extract_fallback_evidence(scrape, 'ovhcloud.com')
    assert len(result) == 1 and result[0].excerpt == sentence and result[0].claim == sentence


def test_relevance_score_is_bounded_and_explainable():
    analysis = CompanyAnalysis(company_name='Acme', ai_topics=['machine learning'], technologies=['Python'], summary='AI data science software')
    score, reasons = calculate_relevance(analysis, job_titles=['Data Scientist'], locations=['Paris'], sectors=['AI'], company_types=['startup'], location='Paris', sector='AI', company_type='startup')
    assert score == 100 and len(reasons) >= 4
    ai_only = CompanyAnalysis(company_name='Acme', summary='AI-assisted product.')
    assert calculate_relevance(ai_only, job_titles=[], locations=[], sectors=[], company_types=[])[0] == 35
    empty = CompanyAnalysis(company_name='X')
    assert calculate_relevance(empty, job_titles=[], locations=[], sectors=[], company_types=[])[0] == 0


def test_sqlite_migration_adds_company_analysis_fields():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, domain VARCHAR(255) NOT NULL UNIQUE, website TEXT NOT NULL, location VARCHAR, sector VARCHAR, size VARCHAR, description TEXT, linkedin_url TEXT, career_url TEXT, source_url TEXT)')
        connection.exec_driver_sql("INSERT INTO companies (name,domain,website) VALUES ('Legacy','legacy.test','https://legacy.test')")
    init_db(engine)
    columns = {x['name'] for x in inspect(engine).get_columns('companies')}
    assert {'normalized_name', 'analysis_json', 'analysis_updated_at', 'company_relevance_score', 'official_domain_confidence'} <= columns
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT name FROM companies WHERE id=1').scalar_one() == 'Legacy'


def test_discovery_verifies_and_deduplicates_by_domain(session, monkeypatch):
    class Source:
        def search(self, query, limit):
            return [CompanyCandidate('Acme SAS', 'https://www.acme.test/', source_type='search'), CompanyCandidate('Acme', 'http://acme.test/fr', source_type='search')]
    response = Mock(status_code=200, url='https://acme.test/', headers={'content-type':'text/html'}, text='<title>Acme</title>')
    response.raise_for_status.return_value = None
    monkeypatch.setattr('agents.company_discovery.httpx.get', lambda *a, **k: response)
    agent = CompanyDiscoveryAgent(sources=[Source()])
    result = agent.discover(session, job_titles=['AI Engineer'], locations=[], company_types=[], sectors=[], max_companies=5)
    assert len(result) == 1 and result[0].domain == 'acme.test'
    again = agent.discover(session, job_titles=['AI Engineer'], locations=[], company_types=[], sectors=[], max_companies=5)
    assert len(again) == 1 and again[0].id == result[0].id


def test_domain_confidence_rejects_third_party_or_mismatch(monkeypatch):
    assert _verified_official(CompanyCandidate('Acme', 'https://linkedin.com/company/acme')) is None
    response = Mock(status_code=200, url='https://unrelated.test/', headers={'content-type':'text/html'}, text='<title>Unrelated</title>')
    response.raise_for_status.return_value = None
    monkeypatch.setattr('agents.company_discovery.httpx.get', lambda *a, **k: response)
    value = _verified_official(CompanyCandidate('Acme', 'https://unrelated.test/', source_type='manual'))
    assert value and value[2] < 0.75


def test_analysis_persists_results_and_uses_cache(session):
    company = Company(name='Acme', normalized_name='acme', domain='acme.test', website='https://acme.test/', website_url='https://acme.test/')
    session.add(company); session.commit(); session.refresh(company)
    scraper = Mock()
    page = Mock(url='https://acme.test/', text='Title: Acme\nWe build data tools for researchers.')
    scraper.scrape.return_value = WebsiteResult('https://acme.test/', [page], elapsed_seconds=0.1)
    provider = Mock(); provider.model_name = 'local/test'
    provider.last_usage = {'prompt_tokens': 100, 'completion_tokens': 20}
    provider.generate.return_value = json.dumps({'company_name':'Acme', 'summary':'Builds data tools.', 'main_activities':['Data tools'], 'technologies':['Python'], 'ai_data_relevance':'Relevant data activity.', 'ai_topics':['data science'], 'evidence':[{'claim':'Builds data tools', 'url':'https://acme.test/', 'excerpt':'We build data tools for researchers.'}], 'analysis_confidence':0.9})
    agent = CompanyAnalysisAgent(provider, scraper=scraper)
    result = agent.analyze(session, company, criteria={'job_titles':['Data Scientist']})
    assert result['company_relevance_score'] > 0 and company.analysis_updated_at
    assert result['llm_total_tokens'] == 120
    cached = agent.analyze(session, company)
    assert cached['analysis_cached'] is True and provider.generate.call_count == 1
    agent.analyze(session, company, force=True)
    assert provider.generate.call_count == 2


@pytest.mark.integration
def test_public_website_scrape_optional():
    from scrapers.website import WebsiteScraper
    result = WebsiteScraper(max_pages=2).scrape('https://www.python.org/')
    assert result.pages and result.pages[0].title
