import json

from discovery.llm_company_source import LLMCompanySource, _parse_names


def test_company_names_reject_anonymous_and_deduplicate():
    names = _parse_names(json.dumps({"companies": ["Qonto", "Qonto", "Confidential Company", "Anonymous", "Mistral AI", "Doctolib"]}), 10)
    assert names == ["Qonto", "Doctolib"]


def test_llm_company_source_uses_web_results_not_model_urls():
    class Provider:
        def generate(self, *args, **kwargs):
            assert kwargs["json_mode"] is True
            return '{"companies":["Doctolib", "Anonymous Company"]}'

    class Search:
        def __init__(self): self.queries=[]
        def text(self, query, **kwargs):
            self.queries.append(query)
            return [
                {"title":"Doctolib — entreprise française", "href":"https://www.doctolib.fr/about", "body":"Health technology company"},
                {"title":"Doctolib jobs", "href":"https://www.linkedin.com/company/doctolib", "body":"Doctolib France"},
            ]
        def close(self): pass

    search=Search()
    candidates=LLMCompanySource(Provider(),search_factory=lambda:search).search("IA en France",5)
    assert len(candidates)==1
    assert candidates[0].company_name=="Doctolib"
    assert candidates[0].website_url=="https://doctolib.fr/"
    assert candidates[0].source_type=="llm_web_search"
    assert len(search.queries)==1
