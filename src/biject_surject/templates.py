"""The default ``surject.yaml`` written by ``surject init``."""

DEFAULT_POLICY = """\
# Surject policy. Surject checks every tool call your agent makes against
# this file and speaks up only when a call breaks a rule.
#
# Docs: https://github.com/bijectai/surject-development
# Validate after editing:  surject validate

version: 1

# monitor: flag violations and let the call run (safe default)
# enforce: also block the call; the agent receives an error instead
mode: monitor

# What to do with tools that are not listed under `tools:` below.
#   allow - ignore them
#   warn  - flag every call to an unlisted tool (good for discovering what
#           your agent actually calls)
#   block - treat them as violations (strict allowlist)
unknown_tools: allow

# Rules per tool, keyed by the tool's name as your agent framework calls it.
# A tool can be `allow`, `block`, or a mapping of param rules + rate limit.
#
# Param rules:
#   required: true            the param must be present
#   type: string              string | integer | number | boolean | array | object | null
#   equals: value             exact value
#   one_of: [a, b]            one of a list
#   regex: "^https://"        must match (anchor with ^ and $ for whole-string)
#   glob: ["./data/**"]       path must be inside one of these
#   not_glob: ["./.env"]      path must not be inside any of these
#   min: 1 / max: 100         numeric bounds
#   max_length: 1000          max length of a string, list or mapping
#
# Paths are matched relative to this file's folder; `**` spans directories.
tools:
  write_file:
    params:
      path:
        not_glob:
          - "**/.env"
          - "**/*.pem"
          - "**/*.key"
          - "./.git/**"

  delete_file:
    params:
      path:
        not_glob:
          - "./.git/**"
          - "**/.env"

  # Examples. Uncomment and rename to match your agent's tools.
  #
  # read_file:
  #   params:
  #     path: { glob: ["./data/**", "./src/**"] }
  #
  # http_get:
  #   params:
  #     url: { regex: "^https://api\\\\.example\\\\.com/" }
  #   rate_limit: { max: 30, per: 60s }
  #
  # drop_table: block

# Rate limit across all tools combined.
# rate_limit: { max: 120, per: 60s }

# Secrets are always redacted from everything Surject prints or writes.
# Add your own sensitive param names or value patterns here (you cannot
# turn the built-in redaction off).
# redact:
#   names: [patient_id]
#   patterns: ["MRN-\\\\d+"]
"""
