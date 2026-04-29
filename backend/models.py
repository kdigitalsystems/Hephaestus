from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()

class Node(Base):
    __tablename__ = 'nodes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    ticker = Column(String, unique=True, nullable=True) 
    
    entity_type = Column(String, nullable=False, default="Company") 
    sector = Column(String, default="Uncategorized")
    industry = Column(String, default="Uncategorized")
    
    # Core Financials
    current_price = Column(Float, nullable=True)
    percent_change = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)
    enterprise_value = Column(Float, nullable=True)
    
    # Deep Metrics
    trailing_pe = Column(Float, nullable=True)
    forward_pe = Column(Float, nullable=True)
    price_to_book = Column(Float, nullable=True)
    dividend_yield = Column(String, nullable=True)
    fifty_two_week_high = Column(Float, nullable=True)
    fifty_two_week_low = Column(Float, nullable=True)
    
    # Health & Margins
    total_revenue = Column(Float, nullable=True)
    gross_margin = Column(Float, nullable=True)
    
    # Sentiment
    target_price = Column(Float, nullable=True)
    recommendation = Column(String, nullable=True)
    
    # Corporate Profile
    ceo_name = Column(String, nullable=True)
    employees = Column(Integer, nullable=True)
    business_summary = Column(Text, nullable=True)
    
    hierarchy_level = Column(Integer, default=2) 
    metadata_json = Column(String, nullable=True) 
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    supplied_by = relationship("Edge", foreign_keys="[Edge.target_id]", back_populates="target_node")
    supplies_to = relationship("Edge", foreign_keys="[Edge.source_id]", back_populates="source_node")

    def __repr__(self):
        return f"<Node(name='{self.name}', ticker='{self.ticker}')>"

class Edge(Base):
    __tablename__ = 'edges'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    target_id = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    dependency_type = Column(String, nullable=False)
    confidence_score = Column(Float, nullable=True)
    source_url = Column(String, nullable=True)
    last_verified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    source_node = relationship("Node", foreign_keys=[source_id], back_populates="supplies_to")
    target_node = relationship("Node", foreign_keys=[target_id], back_populates="supplied_by")
