"""Shared validated schemas and ATS detection for public career sources."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

class JobOfferData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    title: str = Field(min_length=1, max_length=500)
    location: str | None = None
    country: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    department: str | None = None
    description: str | None = None
    requirements: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    job_url: str
    source_type: str = "generic"
    external_id: str | None = None
    published_at: datetime | None = None
    is_potentially_relevant: bool = False
    relevance_keywords: list[str] = Field(default_factory=list)
    @field_validator("published_at", mode="before")
    @classmethod
    def parse_date(cls, value: Any):
        if isinstance(value, str):
            try: return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError: return None
        return value

class CareerScanResult(BaseModel):
    career_url: str | None = None
    ats_type: str | None = None
    jobs: list[JobOfferData] = Field(default_factory=list)
    status: str = "complete"
    error: str | None = None
    requests: int = 0
    elapsed_seconds: float = 0.0

ATS_HOSTS = {
    "greenhouse": ("greenhouse.io", "greenhouse.com"), "lever": ("lever.co",),
    "workday": ("myworkdayjobs.com", "workday.com"), "smartrecruiters": ("smartrecruiters.com",),
    "ashby": ("ashbyhq.com",), "welcome_to_the_jungle": ("welcometothejungle.com", "wttj.co"),
    "teamtailor": ("teamtailor.com",),
}
def detect_ats(url: str) -> str | None:
    host = (url.split("/", 3)[2] if "://" in url else url).lower().split(":")[0]
    return next((name for name, domains in ATS_HOSTS.items() if any(host == d or host.endswith("." + d) for d in domains)), None)
