from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()

class Node(Base):
    """
    Represents an entity in the supply chain (e.g., a Company, Raw Material, or Sector).
    """
    __tablename__ = 'nodes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    
    # E.g., 'Company', 'Material', 'Category'
    entity_type = Column(String, nullable=False) 
    
    # Level 0, 1, or 2 based on your hierarchy
    hierarchy_level = Column(Integer, default=2) 
    
    # For storing financial data like P/E ratios or revenue growth
    metadata_json = Column(String, nullable=True) 
    
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships for the graph
    supplied_by = relationship("Edge", foreign_keys="[Edge.target_id]", back_populates="target_node")
    supplies_to = relationship("Edge", foreign_keys="[Edge.source_id]", back_populates="source_node")

    def __repr__(self):
        return f"<Node(name='{self.name}', type='{self.entity_type}')>"


class Edge(Base):
    """
    Represents the dependency connection between two Nodes.
    """
    __tablename__ = 'edges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # The supplier
    source_id = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    
    # The buyer / dependent
    target_id = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    
    # E.g., 'Supplies Memory', 'Provides Fab Equipment'
    dependency_type = Column(String, nullable=False)
    
    # Used to verify if the LLM hallucinated the connection (0.0 to 1.0)
    confidence_score = Column(Float, nullable=True)
    
    # Source link to the SEC filing or news article
    source_url = Column(String, nullable=True)
    
    last_verified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    source_node = relationship("Node", foreign_keys=[source_id], back_populates="supplies_to")
    target_node = relationship("Node", foreign_keys=[target_id], back_populates="supplied_by")

    def __repr__(self):
        return f"<Edge(source_id={self.source_id}, target_id={self.target_id}, type='{self.dependency_type}')>"
