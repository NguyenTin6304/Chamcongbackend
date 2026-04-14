from pydantic import BaseModel


class UserLiteResponse(BaseModel):
    id: int
    email: str
    role: str
    full_name: str | None = None
    phone: str | None = None
