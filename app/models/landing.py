from sqlalchemy import Column, Text, Integer, String, Boolean, DateTime, ForeignKey, Index, Enum as SQLAEnum
from app.common.database_config import Base

class UserGuide(Base):
    __tablename__ = 'user_guide'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Appearance fields
    title = Column(String)
    link = Column(String(255), nullable=True)
    description =Column(Text, nullable=False, default=None)


class ExternalLinks(Base):
    __tablename__ = 'external_links'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Appearance fields
    link = Column(String(255), nullable=True)
    title = Column(String)
    description =Column(Text, nullable=False, default=None)


class FileDownloads(Base):
    __tablename__ = 'file_downloads'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Appearance fields
    link = Column(String(255), nullable=True)
    title = Column(String)
    description =Column(Text, nullable=False, default=None)


class VideosTutorials(Base):
    __tablename__ = 'video_tutorials'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Appearance fields
    link = Column(String(255), nullable=True)
    title = Column(String)
    description =Column(Text, nullable=False, default=None)


class Faqs(Base):
    __tablename__ = 'faqs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Appearance fields
    question = Column(String(255), nullable=True)
    answer = Column(String)
