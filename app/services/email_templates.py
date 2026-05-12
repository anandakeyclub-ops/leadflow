def _first_name(lead_name: str) -> str:
    return (lead_name or "").split(" ")[0].title() if lead_name else "there"


def build_initial_email(lead_name: str, booking_link: str):
    first_name = _first_name(lead_name)
    subject = "Question about your tax situation"
    body = f"""Hi {first_name},

I came across a public record that suggests there may be an unresolved tax issue tied to you or your business.

Sometimes these situations are already being handled. Sometimes they are easier to fix before they escalate.

If you want, you can start with a quick case review here:
{booking_link}

No pressure.

Dana
"""
    return subject, body


def build_followup_email(lead_name: str, booking_link: str):
    first_name = _first_name(lead_name)
    subject = "Following up"
    body = f"""Hi {first_name},

Just following up in case my earlier note got buried.

If there is an IRS issue in the background, this is the fastest way to review your options:
{booking_link}

Dana
"""
    return subject, body


def build_closeout_email(lead_name: str, booking_link: str):
    first_name = _first_name(lead_name)
    subject = "I will close this out"
    body = f"""Hi {first_name},

I have not heard back, so I will assume this is already being handled.

If you want a quick review later, you can use this link:
{booking_link}

Dana
"""
    return subject, body
