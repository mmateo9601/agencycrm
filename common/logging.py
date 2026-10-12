import logging
import re


class WebhookQueryFilter(logging.Filter):
    """Remove subscription query secrets from the development access log."""

    def filter(self, record):
        record.msg = re.sub(
            r"(/integrations/whatsapp/[^?\s\"]+)\?[^\s\"]+", r"\1?[redacted]", record.getMessage()
        )
        record.args = ()
        return True
