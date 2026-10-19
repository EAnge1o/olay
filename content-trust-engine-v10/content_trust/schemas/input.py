"""Public analysis request schema."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from content_trust.schemas.evidence import EvidenceItem


class CommentInput(BaseModel):
    """One supplied social-media comment; metadata is optional and untrusted."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    author: str | None = None
    published_at: str | None = None
    region: str | None = None


class TrustInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    text: str | None = None
    image_path: str | None = Field(default=None, min_length=1)
    image_url: str | None = Field(default=None, min_length=1)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    comments: list[CommentInput] = Field(default_factory=list)
    comment_scope: str | None = None
    locale: str = "zh-CN"

    @model_validator(mode="after")
    def image_source_is_unambiguous(self) -> "TrustInput":
        if (
            self.text is None
            and self.image_path is None
            and self.image_url is None
            and not self.comments
        ):
            raise ValueError("Provide at least one of: text, an image, or comments")
        if self.image_path is not None and self.image_url is not None:
            raise ValueError("Provide at most one of image_path and image_url")
        comment_ids = [comment.id for comment in self.comments]
        if len(comment_ids) != len(set(comment_ids)):
            raise ValueError("Comment ids must be unique")
        return self


class TrustImageInput(BaseModel):
    """One image in a multi-image post."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    image_path: str | None = Field(default=None, min_length=1)
    image_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def has_one_image_source(self) -> "TrustImageInput":
        if self.image_path is None and self.image_url is None:
            raise ValueError("Provide image_path or image_url")
        if self.image_path is not None and self.image_url is not None:
            raise ValueError("Provide at most one of image_path and image_url")
        return self


class BatchTrustInput(BaseModel):
    """Shared post text and evidence plus all images belonging to that post."""

    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    text: str | None = None
    images: list[TrustImageInput] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    comments: list[CommentInput] = Field(default_factory=list)
    comment_scope: str | None = None
    locale: str = "zh-CN"

    @model_validator(mode="after")
    def has_content(self) -> "BatchTrustInput":
        if self.text is None and not self.images and not self.comments:
            raise ValueError("Provide text, at least one image, or comments")
        image_ids = [image.id for image in self.images]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("Image ids must be unique")
        comment_ids = [comment.id for comment in self.comments]
        if len(comment_ids) != len(set(comment_ids)):
            raise ValueError("Comment ids must be unique")
        return self
