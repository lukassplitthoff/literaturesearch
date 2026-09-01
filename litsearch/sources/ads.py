"""NASA ADS -- DEFERRED, not yet implemented.

ADS has the strongest physics coverage of the four indexes, but it is the only one
requiring a credential: a free token from https://ui.adsabs.harvard.edu -> Account ->
API Token, supplied as ADS_API_TOKEN in a gitignored .env.

Until that token exists this source reports itself unavailable and the run proceeds on
the keyless sources. It never raises, so enabling it early cannot break a search.

To implement, when the token is in hand:
    GET https://api.adsabs.harvard.edu/v1/search/query
        headers: {"Authorization": "Bearer <token>"}
        params:  q=<query>, fl=title,author,year,doi,identifier,abstract,citation_count,bibcode
    then map the response onto Work exactly as inspire.to_work does.
"""

from __future__ import annotations

import os

from litsearch.sources.base import Fetcher, Work

NAME = "ads"
TOKEN_ENV = "ADS_API_TOKEN"


def available() -> bool:
    """True once a token is configured. Checked before the source is used."""
    return bool(os.environ.get(TOKEN_ENV))


def search(fetcher: Fetcher, query: str, limit: int = 50, year_from=None, year_to=None) -> list[Work]:
    """Deferred. Returns nothing and explains itself rather than failing the run."""
    if not available():
        print(f"  [skip] NASA ADS: no {TOKEN_ENV} set -- deferred, see litsearch/sources/ads.py")
        return []
    print("  [skip] NASA ADS: token present but the client is not implemented yet")
    return []
