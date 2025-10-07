from typing import Optional, Any, List, Tuple, Literal
from datetime import datetime
from pydantic import BaseModel
try:
    from pydantic import ConfigDict
    V2 = True
except Exception:
    V2 = False

# users
class EnsureUserIn(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UserProfileOut(BaseModel):
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    roles: list[str]
    groups: list[dict]

# subjects
class SubjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    group_ids: list[int]

class SubjectOutFull(SubjectOut):
    pass

class SubjectCreateIn(BaseModel):
    name: str
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None

class SubjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    group_ids: Optional[list[int]] = None

# lessons
class LessonBlockIn(BaseModel):
    type: Literal["text","image"]
    position: Optional[int] = None
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonBlockOut(BaseModel):
    position: int
    type: str
    text: Optional[str] = None
    image_url: Optional[str] = None
    caption: Optional[str] = None

class LessonCreateIn(BaseModel):
    subject_id: int
    title: str
    publish: bool = False
    publish_at: Optional[datetime] = None
    group_ids: Optional[list[int]] = None
    user_tg_ids: Optional[list[int]] = None
    pdf_url: Optional[str] = None
    blocks: Optional[list[LessonBlockIn]] = None
    html_content: Optional[str] = None

class LessonPatchIn(BaseModel):
    title: Optional[str] = None
    publish: Optional[bool] = None
    publish_at: Optional[datetime] = None
    group_ids: Optional[list[int]] = None
    user_tg_ids: Optional[list[int]] = None
    pdf_url: Optional[str] = None
    blocks: Optional[list[LessonBlockIn]] = None
    html_content: Optional[str] = None

class LessonOut(BaseModel):
    id: int
    title: str
    status: Optional[str] = None
    publish_at: Optional[datetime] = None
    subject_id: Optional[int] = None
    subject_name: Optional[str] = None
    group_ids: List[int] = []
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    blocks: Optional[List[Any]] = None
    if V2:
        model_config = ConfigDict(from_attributes=True)
    else:
        class Config: orm_mode = True
    html_content: Optional[str] = None
    html_url: Optional[str] = None

class LessonDetailOut(LessonOut):
    blocks: list[LessonBlockOut] = []
    group_ids: list[int] = []
    html_content: Optional[str] = None
    html_url: Optional[str] = None

class LessonListItemOut(BaseModel):
    id: int
    subject_id: int
    title: str
    status: str
    publish_at: Optional[datetime] = None
    group_ids: list[int]

# roles & groups
class RoleMemberIn(BaseModel):
    tg_id: int

class GroupMemberIn(BaseModel):
    tg_id: int

class GroupCreateIn(BaseModel):
    name: str

class GroupPatchIn(BaseModel):
    name: str
