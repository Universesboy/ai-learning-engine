import pytest


@pytest.fixture(autouse=True)
def fake_embedder():
    """Tests never load the real 1 GB embedding model."""
    from lengine import embed
    from tests.fake_llm import FakeEmbedder
    embed.set_embedder(FakeEmbedder())
    yield
    embed.set_embedder(None)
