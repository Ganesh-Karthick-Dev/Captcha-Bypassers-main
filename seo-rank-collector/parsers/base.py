"""Base SERP Parser interface for SEO Rank Collector."""
from abc import ABC, abstractmethod
from typing import List
from rank_models import SERPItem


class BaseSERPParser(ABC):
    """Abstract interface for search engine result page parsers."""

    @abstractmethod
    def parse(self, html: str, page_number: int = 1) -> List[SERPItem]:
        """Parses HTML content of a SERP page into a list of SERPItem objects.

        :param html: Raw HTML response string from the search engine.
        :param page_number: Current page index in pagination (1, 2, ...).
        :return: List of structured SERPItem instances.
        """
        pass
