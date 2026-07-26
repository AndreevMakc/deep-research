import hashlib

from pydantic import (
    BaseModel,
    Field,
    model_validator,
)


class SourceDocument(BaseModel):
    """Full text downloaded from one research source."""

    requested_url: str = Field(
        min_length=8,
        max_length=4000,
    )
    url: str = Field(
        min_length=8,
        max_length=4000,
        description="Final URL after validated redirects.",
    )
    canonical_url: str = Field(
        min_length=8,
        max_length=4000,
    )
    title: str | None = Field(
        default=None,
        max_length=1000,
    )
    content: str = Field(
        min_length=1,
        description="Normalized full text of the source.",
    )
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    mime_type: str = Field(
        min_length=1,
        max_length=100,
    )
    http_status: int | None = Field(
        default=None,
        ge=100,
        le=599,
    )
    metadata_json: dict = Field(
        default_factory=dict,
    )

    @model_validator(mode="after")
    def validate_content_hash(self):
        expected_hash = hashlib.sha256(
            self.content.encode("utf-8")
        ).hexdigest()

        if self.content_hash != expected_hash:
            raise ValueError(
                "content_hash does not match source content"
            )

        return self
