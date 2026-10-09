"""The salon's service catalogue: what Aria may SAY (informational) versus what Aria may BOOK (verified mappings).

Two deliberately separate things:
  * ``website_data`` / ``models``: facts published on the salon's official website (name, description, listed price,
    listed duration), each with its source page and the date it was read. Informational only.
  * ``bookable``: the allowlist of services that are mapped to a real Booksy service with a verified duration and
    staff eligibility. Only these can ever be booked. A website service with no mapping can be discussed, never booked.
"""
