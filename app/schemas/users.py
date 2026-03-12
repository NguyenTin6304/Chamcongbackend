from pydantic import BaseModel


class UserLiteResponse(BaseModel):
    id: int
    email: str
    role: str
