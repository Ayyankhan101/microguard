"""Tests for RedisRuntimeConfig — the shared, operator-settable block threshold."""

import pytest
import redis

from microguard.live.runtime_config import RedisRuntimeConfig


@pytest.fixture(scope="module")
def redis_client():
    try:
        client = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        client.ping()
        yield client
        client.flushdb()
        client.close()
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


@pytest.fixture()
def config(redis_client):
    redis_client.flushdb()
    return RedisRuntimeConfig(redis_client, cache_seconds=0.0)


def test_unset_threshold_reads_as_none(config):
    assert config.block_threshold() is None


def test_a_written_threshold_reads_back(config):
    config.set_block_threshold(0.42)

    assert config.block_threshold() == 0.42


def test_the_override_is_visible_to_another_process(config, redis_client):
    config.set_block_threshold(0.42)

    other = RedisRuntimeConfig(redis_client, cache_seconds=0.0)

    assert other.block_threshold() == 0.42


def test_clearing_the_override_restores_the_configured_default(config):
    config.set_block_threshold(0.42)

    config.set_block_threshold(None)

    assert config.block_threshold() is None


def test_a_corrupt_stored_value_reads_as_none_rather_than_raising(config, redis_client):
    redis_client.hset("mg:v1:config", "block_threshold", "not-a-number")

    assert config.block_threshold() is None


def test_the_value_is_cached_so_scoring_does_not_hit_redis_every_request(redis_client):
    redis_client.flushdb()
    cached = RedisRuntimeConfig(redis_client, cache_seconds=60.0)
    cached.set_block_threshold(0.42)
    cached.block_threshold()

    redis_client.hset("mg:v1:config", "block_threshold", "0.99")

    assert cached.block_threshold() == 0.42


def test_the_cache_expires(redis_client):
    redis_client.flushdb()
    cached = RedisRuntimeConfig(redis_client, cache_seconds=0.0)
    cached.set_block_threshold(0.42)
    cached.block_threshold()

    cached.set_block_threshold(0.99)

    assert cached.block_threshold() == 0.99


def test_a_redis_outage_reads_as_none_so_the_configured_threshold_stands(config):
    broken = RedisRuntimeConfig(
        redis.Redis(host="127.0.0.1", port=6390, socket_connect_timeout=0.05),
        cache_seconds=0.0,
    )

    assert broken.block_threshold() is None


def test_out_of_range_thresholds_are_rejected_on_write(config):
    with pytest.raises(ValueError):
        config.set_block_threshold(1.5)
