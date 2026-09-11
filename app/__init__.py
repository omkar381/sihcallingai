"""AI Krishi package bootstrap and runtime compatibility shims."""

import sys


def _ensure_cgi_compatibility() -> None:
	"""Provide a minimal cgi.parse_header for libs that still import cgi."""
	if "cgi" in sys.modules:
		return

	import email.message

	class MockCGI:
		@staticmethod
		def parse_header(line):
			message = email.message.Message()
			message["content-type"] = line
			params = message.get_params()
			if not params:
				return "", {}
			return params[0][0], dict(params[1:])

	sys.modules["cgi"] = MockCGI


_ensure_cgi_compatibility()
