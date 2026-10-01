# ponytail: 2 sync workers, each with exactly one DB connection (D28), so at most 2 requests run at once
# and a slow Payment/Access call (timeout=5) holds a worker. Upgrade path: psycopg_pool plus threads.
workers = 2
bind = "0.0.0.0:8000"

# Request log to stdout. %(U)s is the path without the query string, and the referer is left out,
# because URLs can carry values that must not be logged.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms "%(a)s"'
