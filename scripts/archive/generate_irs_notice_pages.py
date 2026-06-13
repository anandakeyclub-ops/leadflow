"""
generate_irs_notice_pages.py
============================
Generates IRS notice explainer pages for taxcasereview.org.
These target high-intent searches like "CP14 notice IRS what to do".

Each page:
- Explains exactly what the notice means
- Shows the escalation timeline
- Answers top 5 questions
- Drives to the quiz/booking

Usage:
  python generate_irs_notice_pages.py
"""
from pathlib import Path

NOTICES = [
    {
        "code": "CP14",
        "slug": "cp14-notice",
        "title": "IRS CP14 Notice — What It Means and What to Do",
        "meta_desc": "Received an IRS CP14 notice? This is the first formal demand for payment. Learn what CP14 means, your options, and how to respond. Florida tax professionals available.",
        "h1": "Received an IRS CP14 Notice? Here's Exactly What It Means.",
        "direct_answer": "A CP14 notice means the IRS believes you owe taxes and this is their first formal request for payment. It is not a lien or a levy — but it is the first step in an escalation process that can lead to both. You have approximately 60 days to respond before the next notice is issued.",
        "what_it_is": "CP14 is the IRS's first formal balance-due notice. It's generated automatically when your tax return shows a balance and the IRS has not received payment. It includes the amount owed, the tax year, and a due date — typically 21 days from the notice date.",
        "urgency": "Moderate — first notice, but ignoring it starts the clock on penalties and escalation.",
        "next_steps": [
            "Verify the amount is correct by reviewing your return and IRS records",
            "Determine if you can pay in full, need a payment plan, or qualify for other relief",
            "Respond within 60 days — either with payment or a written response",
            "Do NOT ignore this notice — silence triggers CP501 within 5 weeks",
        ],
        "escalation": ["CP14", "CP501 (5 weeks)", "CP503 (5 weeks)", "CP504 — Final Notice (30 days)", "Federal Tax Lien Filed", "Bank Levy / Wage Garnishment"],
        "faqs": [
            ("What happens if I ignore a CP14 notice?", "If ignored, the IRS issues a CP501 within 5 weeks, then CP503, then CP504 (Final Notice Before Levy). After CP504, the IRS can file a federal tax lien and begin collection action including bank levies and wage garnishments."),
            ("Is a CP14 the same as a tax lien?", "No. A CP14 is a payment demand notice. A federal tax lien is filed separately and appears on public record. However, ignoring a CP14 is one of the fastest paths to having a lien filed against you."),
            ("Can I dispute a CP14 notice?", "Yes. If you believe the amount is incorrect, you can respond in writing with supporting documentation. Common errors include payments not credited, incorrect filing status, or math errors on your return."),
            ("How long do I have to respond to a CP14?", "The due date is printed on the notice — typically 21 days from the notice date. However, you should respond within 60 days to preserve your appeal rights if you disagree with the balance."),
            ("Can I set up a payment plan after a CP14?", "Yes — and this is often the best option for people who cannot pay in full. The IRS offers installment agreements for balances under $50,000 that can be set up online. A tax professional can often negotiate better terms."),
        ],
    },
    {
        "code": "CP503",
        "slug": "cp503-notice",
        "title": "IRS CP503 Notice — Second Reminder, Time Is Running Out",
        "meta_desc": "IRS CP503 notice means you've already received a CP14 and CP501. This is the third escalation — a levy could follow within weeks. See your options now.",
        "h1": "IRS CP503 Notice — What Happens Next If You Don't Act",
        "direct_answer": "A CP503 notice is the IRS's second reminder that you have an unpaid tax balance. At this point, the IRS has already sent CP14 and CP501. The next step after CP503 is CP504 — the Final Notice Before Levy — which means the IRS can begin seizing bank accounts and garnishing wages.",
        "what_it_is": "CP503 is the third notice in the IRS collection sequence. By the time you receive it, penalties and interest have already been accruing for weeks. The IRS is signaling that enforcement action is approaching.",
        "urgency": "High — next step is CP504, which triggers enforcement authority.",
        "next_steps": [
            "Do not ignore this notice — CP504 follows within 30 days",
            "If you can pay, pay the full balance immediately to stop escalation",
            "If you cannot pay, contact a tax professional TODAY to set up an installment agreement or explore other options",
            "Check your IRS account online at irs.gov/account to see your full balance",
        ],
        "escalation": ["✓ CP14 (sent)", "✓ CP501 (sent)", "→ CP503 (YOU ARE HERE)", "CP504 — Final Notice (30 days)", "Federal Tax Lien Filed", "Bank Levy / Wage Garnishment"],
        "faqs": [
            ("How serious is a CP503 notice?", "Very serious. CP503 means the IRS has already sent two previous notices and is escalating toward enforcement. CP504 — which authorizes bank levies and wage garnishments — typically follows within 30 days."),
            ("Can I still set up a payment plan after CP503?", "Yes — but you need to act immediately. An installment agreement must be set up before CP504 is issued to prevent levy action. A tax professional can often have an agreement in place within 24-48 hours."),
            ("What's the difference between CP503 and a tax lien?", "CP503 is a notice — it has no legal effect on your property yet. A federal tax lien is a legal claim filed with the county clerk that appears on public record. If you receive CP504 and don't respond, a lien is typically the next step."),
            ("Will the IRS negotiate at the CP503 stage?", "Yes — and they often prefer it. The IRS would rather set up a payment arrangement than pursue costly enforcement. However, you must proactively contact them or have a professional do so before CP504 is issued."),
            ("I can't afford to pay — what are my options at CP503?", "Options include: installment agreement (pay over time), offer in compromise (settle for less if you qualify), currently not collectible status (pause collection due to hardship), or penalty abatement (reduce what you owe). A tax professional can identify which applies to your situation."),
        ],
    },
    {
        "code": "CP504",
        "slug": "cp504-notice",
        "title": "IRS CP504 Notice — Final Warning Before Levy | Act Now",
        "meta_desc": "CP504 is the IRS Final Notice Before Levy. Bank accounts and wages can be seized within 30 days. Florida tax professionals available for urgent cases. Call (561) 247-0678.",
        "h1": "IRS CP504 Notice — This Is a Final Warning. Here's What Happens Next.",
        "direct_answer": "A CP504 notice is the IRS's Final Notice of Intent to Levy. This means the IRS now has legal authority to seize your state tax refund immediately and can levy your bank accounts and garnish your wages within 30 days. This is the most urgent IRS notice — action within 24-48 hours is critical.",
        "what_it_is": "CP504 is the fourth and final notice in the standard IRS collection sequence. Upon issuing CP504, the IRS can immediately seize your state tax refund. After 30 days, they can levy bank accounts, garnish wages, and seize other assets. A federal tax lien is typically filed around this time as well.",
        "urgency": "CRITICAL — levy authority is active. Contact a tax professional today.",
        "next_steps": [
            "Call a tax professional TODAY — (561) 247-0678 (urgent cases prioritized)",
            "Request a Collection Due Process hearing within 30 days to pause levy action",
            "Do NOT withdraw money from bank accounts — this can trigger faster action",
            "Gather your most recent tax returns, IRS notices, and financial documents",
        ],
        "escalation": ["✓ CP14 (sent)", "✓ CP501 (sent)", "✓ CP503 (sent)", "→ CP504 — YOU ARE HERE (levy authority active)", "Bank Levy / Wage Garnishment (30 days)", "Federal Tax Lien Filed (may already be filed)"],
        "faqs": [
            ("How much time do I have after a CP504?", "The IRS can seize your state tax refund immediately. Bank levies and wage garnishments can begin after 30 days. However, you can pause this by requesting a Collection Due Process (CDP) hearing within 30 days of the CP504 date."),
            ("Can a tax lien already be filed after CP504?", "Yes — the IRS often files a federal tax lien around the same time as CP504. This appears on public record and affects your credit, property, and ability to sell or refinance."),
            ("What is a Collection Due Process hearing?", "A CDP hearing is your legal right to appeal IRS collection action. Requesting one within 30 days of CP504 pauses the levy while your case is reviewed. A tax professional can file this request and negotiate on your behalf."),
            ("Will the IRS still accept a payment plan at CP504?", "Yes — but you must act immediately. The IRS generally prefers a payment arrangement over costly enforcement. An experienced tax professional can often halt levy action within 24-48 hours by establishing an installment agreement."),
            ("I received CP504 and I'm terrified — what should I do right now?", "Call us directly at (561) 247-0678. We handle CP504 cases regularly and can often halt levy action quickly. The worst thing you can do is wait. Our $399 case review includes same-week response for CP504 situations."),
        ],
    },
]


def generate_notice_page(notice: dict) -> str:
    escalation_items = "\n".join([
        f'            <div style={{{{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}}}>'
        f'<span style={{{{ width: "8px", height: "8px", borderRadius: "50%", background: "{("#D4A843" if "→" in s or "HERE" in s else "rgba(255,255,255,0.2)")}", flexShrink: "0" }}}}></span>'
        f'<span style={{{{ fontSize: "14px", color: "{("#fff" if "→" in s or "HERE" in s else "rgba(255,255,255,0.5)")}", fontWeight: "{("600" if "HERE" in s else "400")}" }}}}>{s.replace("→ ","")}</span>'
        f'</div>'
        for s in notice["escalation"]
    ])

    steps_items = "\n".join([
        f'              <li style={{{{ marginBottom: "12px", lineHeight: "1.6" }}}}>{step}</li>'
        for step in notice["next_steps"]
    ])

    faq_items = "\n".join([
        f'''            <div style={{{{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}}}>
              <h3 style={{{{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}}}>{q}</h3>
              <p style={{{{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}}}>{a}</p>
            </div>'''
        for q, a in notice["faqs"]
    ])

    urgency_color = {"Moderate": "#D4A843", "High": "#E87040", "CRITICAL": "#E84040"}.get(
        notice["urgency"].split(" —")[0], "#D4A843")

    return f'''import type {{ Metadata }} from "next"
import Link from "next/link"

export const metadata: Metadata = {{
  title: "{notice['title']} | TaxCase Review Florida",
  description: "{notice['meta_desc']}",
}}

const faqSchema = {{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
{chr(10).join([f'''    {{ "@type": "Question", "name": "{q}", "acceptedAnswer": {{ "@type": "Answer", "text": "{a}" }} }},''' for q, a in notice['faqs']])}
  ]
}}

export default function {notice['code']}Page() {{
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{{{ __html: JSON.stringify(faqSchema) }}}} />
      <main style={{{{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff" }}}}>

        <div style={{{{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.1)", fontSize: "13px", color: "rgba(255,255,255,0.5)" }}}}>
          <Link href="/" style={{{{ color: "#D4A843", textDecoration: "none" }}}}>Home</Link>
          <span style={{{{ margin: "0 8px" }}}}>›</span>
          <Link href="/irs-notices" style={{{{ color: "#D4A843", textDecoration: "none" }}}}>IRS Notices</Link>
          <span style={{{{ margin: "0 8px" }}}}>›</span>
          <span>{notice['code']}</span>
        </div>

        <section style={{{{ maxWidth: "800px", margin: "0 auto", padding: "60px 24px 48px" }}}}>
          <div style={{{{ display: "inline-block", background: "{urgency_color}22", border: "1px solid {urgency_color}", color: "{urgency_color}", fontSize: "12px", fontWeight: "700", letterSpacing: "0.1em", padding: "4px 12px", borderRadius: "4px", marginBottom: "20px", textTransform: "uppercase" }}}}>
            {notice['urgency']}
          </div>
          <h1 style={{{{ fontSize: "clamp(26px, 4vw, 38px)", fontWeight: "700", lineHeight: "1.2", marginBottom: "24px" }}}}>
            {notice['h1']}
          </h1>
          <div style={{{{ background: "rgba(212,168,67,0.08)", borderLeft: "4px solid #D4A843", padding: "20px 24px", borderRadius: "0 8px 8px 0", marginBottom: "40px" }}}}>
            <p style={{{{ fontSize: "15px", lineHeight: "1.7", color: "rgba(255,255,255,0.85)", margin: 0 }}}}>
              <strong style={{{{ color: "#D4A843" }}}}>What this means: </strong>{notice['direct_answer']}
            </p>
          </div>
          <div style={{{{ display: "flex", gap: "16px", flexWrap: "wrap" }}}}>
            <Link href="/#quiz" style={{{{ background: "#D4A843", color: "#0F1B2D", padding: "14px 28px", borderRadius: "4px", fontWeight: "700", fontSize: "15px", textDecoration: "none" }}}}>
              See My Options Now
            </Link>
            <a href="tel:+15612470678" style={{{{ border: "1px solid rgba(255,255,255,0.3)", color: "#fff", padding: "14px 24px", borderRadius: "4px", fontSize: "15px", textDecoration: "none" }}}}>
              (561) 247-0678
            </a>
          </div>
        </section>

        <section style={{{{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "32px" }}}}>
          <div>
            <h2 style={{{{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}}}>What Is a {notice['code']}?</h2>
            <p style={{{{ fontSize: "15px", color: "rgba(255,255,255,0.75)", lineHeight: "1.7", marginBottom: "24px" }}}}>{notice['what_it_is']}</p>
            <h2 style={{{{ fontSize: "20px", fontWeight: "700", marginBottom: "16px" }}}}>What to Do Now</h2>
            <ol style={{{{ paddingLeft: "20px", color: "rgba(255,255,255,0.75)", fontSize: "15px" }}}}>
{steps_items}
            </ol>
          </div>
          <div>
            <h2 style={{{{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}}}>Where You Are in the Process</h2>
            <div style={{{{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "20px" }}}}>
{escalation_items}
            </div>
          </div>
        </section>

        <section style={{{{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}}}>
          <h2 style={{{{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}}}>Common Questions About {notice['code']}</h2>
{faq_items}
        </section>

        <section style={{{{ background: "rgba(212,168,67,0.1)", padding: "60px 24px", textAlign: "center" }}}}>
          <h2 style={{{{ fontSize: "28px", fontWeight: "700", marginBottom: "16px" }}}}>Get Expert Help With Your {notice['code']} Notice</h2>
          <p style={{{{ color: "rgba(255,255,255,0.7)", marginBottom: "32px", maxWidth: "480px", margin: "0 auto 32px" }}}}>
            Licensed tax professionals · 48-hour response · $399 case review
          </p>
          <Link href="/#quiz" style={{{{ background: "#D4A843", color: "#0F1B2D", padding: "16px 36px", borderRadius: "4px", fontWeight: "700", fontSize: "16px", textDecoration: "none", display: "inline-block" }}}}>
            Start Free Assessment →
          </Link>
        </section>
      </main>
    </>
  )
}}
'''


def main():
    out_dir = Path("./generated_pages/irs-notices")
    out_dir.mkdir(parents=True, exist_ok=True)

    for notice in NOTICES:
        page_dir = out_dir / notice["slug"]
        page_dir.mkdir(exist_ok=True)
        tsx = generate_notice_page(notice)
        (page_dir / "page.tsx").write_text(tsx, encoding="utf-8")
        print(f"  ✓ {notice['code']} → {page_dir}/page.tsx")

    print(f"\n  Generated {len(NOTICES)} IRS notice pages")
    print(f"  Copy generated_pages/irs-notices/ to app/irs-notices/ in your Next.js project")


if __name__ == "__main__":
    main()
