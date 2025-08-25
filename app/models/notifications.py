# from sqlalchemy import Column, Text, Integer, String, Boolean, DateTime, ForeignKey, Index, Enum as SQLAEnum
# from app.common.database_config import Base
# import enum
 main
# class Criticality(str, enum.Enum):
#     NORMAL = "normal"
#     WARNING = "warning"
#     CRITICAL = "critical"

# class Announcements(Base):
#     __tablename__ = "announcememnts"

#     #Primary Key
#     id = Column(Integer, primary_key=True, index=True)

#     #Basic Details
#     title = Column(String)
#     description =Column(Text, nullable=False, default=None)
#     #Role
#     criticality = Column(
#         SQLAEnum(Criticality, native_enum=False),
#         nullable=False,
#         default=Criticality.NORMAL.value
#     )
#     scheduled_time = Column(DateTime, nullable=False, default=None)
    
