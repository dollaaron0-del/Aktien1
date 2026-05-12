from newsapi import NewsApiClient
from datetime import datetime, timedelta
from typing import List, Dict
from config import config


class NewsAPICollector:

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = NewsApiClient(api_key=config.newsapi_key)
        return self._client

    def collect(self, ticker: str, company_name: str = "", days_back: int = 7) -> List[Dict]:
        if not config.newsapi_key:
            return []

        client = self._get_client()
        cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        query = f"{ticker} stock" if not company_name else f"{company_name} OR {ticker} stock"

        results = []
        try:
            response = client.get_everything(
                q=query,
                from_param=cutoff,
                language="en",
                sort_by="relevancy",
                page_size=20,
            )
            for article in response.get("articles", []):
                if article.get("title") == "[Removed]":
                    continue
                published = article.get("publishedAt", "")
                results.append({
                    "source": f"NewsAPI / {article.get('source', {}).get('name', 'unknown')}",
                    "ticker": ticker,
                    "title": article.get("title", ""),
                    "text": f"{article.get('title', '')}. {article.get('description', '') or ''}",
                    "url": article.get("url", ""),
                    "published_at": published,
                })
        except Exception:
            pass

        return results
