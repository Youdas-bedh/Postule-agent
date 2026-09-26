"""Workday pages are identified and parsed only when public markup is present.

Workday often serves JavaScript-only pages; this adapter deliberately does not
reverse-engineer private endpoints or bypass access controls.
"""
from .generic import parse_jobs
def parse_workday(html, page_url):
    return parse_jobs(html, page_url, "workday")
