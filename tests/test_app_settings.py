import pytest
from not_dot_net.config import bookings_config, BookingsConfig


async def test_bookings_config_defaults():
    cfg = await bookings_config.get()
    assert "Windows" in cfg.os_choices
    assert "Ubuntu" in cfg.software_tags


async def test_bookings_config_set_os_choices():
    custom = BookingsConfig(os_choices=["CustomOS"], software_tags={})
    await bookings_config.set(custom)
    cfg = await bookings_config.get()
    assert cfg.os_choices == ["CustomOS"]


async def test_bookings_config_reset():
    custom = BookingsConfig(os_choices=["X"])
    await bookings_config.set(custom)
    await bookings_config.reset()
    cfg = await bookings_config.get()
    assert cfg == BookingsConfig()
