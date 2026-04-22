import enum


class ApplicationStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    applied = "applied"
    follow_up = "follow_up"
    interview = "interview"
    replied = "replied"
    rejected = "rejected"
    offer = "offer"
    archived = "archived"


class DocumentType(str, enum.Enum):
    resume = "resume"
    cover_letter = "cover_letter"
    cold_email = "cold_email"
    cold_message = "cold_message"


class FileType(str, enum.Enum):
    resume = "resume"
    other = "other"
