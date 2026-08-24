"""Configure pytest for the prometheus-telemetry package.

Sets sys.path so that tests can import prometheus_telemetry without __init__.py
in the tests directory (avoids namespace conflict with other workspace packages).
"""
