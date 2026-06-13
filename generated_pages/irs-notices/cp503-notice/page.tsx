import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "IRS CP503 Notice — Second Reminder, Time Is Running Out | TaxCase Review Florida",
  description: "IRS CP503 notice means you've already received a CP14 and CP501. This is the third escalation — a levy could follow within weeks. See your options now.",
}

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    { "@type": "Question", "name": "How serious is a CP503 notice?", "acceptedAnswer": { "@type": "Answer", "text": "Very serious. CP503 means the IRS has already sent two previous notices and is escalating toward enforcement. CP504 — which authorizes bank levies and wage garnishments — typically follows within 30 days." } },
    { "@type": "Question", "name": "Can I still set up a payment plan after CP503?", "acceptedAnswer": { "@type": "Answer", "text": "Yes — but you need to act immediately. An installment agreement must be set up before CP504 is issued to prevent levy action. A tax professional can often have an agreement in place within 24-48 hours." } },
    { "@type": "Question", "name": "What's the difference between CP503 and a tax lien?", "acceptedAnswer": { "@type": "Answer", "text": "CP503 is a notice — it has no legal effect on your property yet. A federal tax lien is a legal claim filed with the county clerk that appears on public record. If you receive CP504 and don't respond, a lien is typically the next step." } },
    { "@type": "Question", "name": "Will the IRS negotiate at the CP503 stage?", "acceptedAnswer": { "@type": "Answer", "text": "Yes — and they often prefer it. The IRS would rather set up a payment arrangement than pursue costly enforcement. However, you must proactively contact them or have a professional do so before CP504 is issued." } },
    { "@type": "Question", "name": "I can't afford to pay — what are my options at CP503?", "acceptedAnswer": { "@type": "Answer", "text": "Options include: installment agreement (pay over time), offer in compromise (settle for less if you qualify), currently not collectible status (pause collection due to hardship), or penalty abatement (reduce what you owe). A tax professional can identify which applies to your situation." } },
  ]
}

export default function CP503Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }} />
      <main style={{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff" }}>

        <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.1)", fontSize: "13px", color: "rgba(255,255,255,0.5)" }}>
          <Link href="/" style={{ color: "#D4A843", textDecoration: "none" }}>Home</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <Link href="/irs-notices" style={{ color: "#D4A843", textDecoration: "none" }}>IRS Notices</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <span>CP503</span>
        </div>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "60px 24px 48px" }}>
          <div style={{ display: "inline-block", background: "#E8704022", border: "1px solid #E87040", color: "#E87040", fontSize: "12px", fontWeight: "700", letterSpacing: "0.1em", padding: "4px 12px", borderRadius: "4px", marginBottom: "20px", textTransform: "uppercase" }}>
            High — next step is CP504, which triggers enforcement authority.
          </div>
          <h1 style={{ fontSize: "clamp(26px, 4vw, 38px)", fontWeight: "700", lineHeight: "1.2", marginBottom: "24px" }}>
            IRS CP503 Notice — What Happens Next If You Don't Act
          </h1>
          <div style={{ background: "rgba(212,168,67,0.08)", borderLeft: "4px solid #D4A843", padding: "20px 24px", borderRadius: "0 8px 8px 0", marginBottom: "40px" }}>
            <p style={{ fontSize: "15px", lineHeight: "1.7", color: "rgba(255,255,255,0.85)", margin: 0 }}>
              <strong style={{ color: "#D4A843" }}>What this means: </strong>A CP503 notice is the IRS's second reminder that you have an unpaid tax balance. At this point, the IRS has already sent CP14 and CP501. The next step after CP503 is CP504 — the Final Notice Before Levy — which means the IRS can begin seizing bank accounts and garnishing wages.
            </p>
          </div>
          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
            <Link href="/#quiz" style={{ background: "#D4A843", color: "#0F1B2D", padding: "14px 28px", borderRadius: "4px", fontWeight: "700", fontSize: "15px", textDecoration: "none" }}>
              See My Options Now
            </Link>
            <a href="tel:+15612470678" style={{ border: "1px solid rgba(255,255,255,0.3)", color: "#fff", padding: "14px 24px", borderRadius: "4px", fontSize: "15px", textDecoration: "none" }}>
              (561) 247-0678
            </a>
          </div>
        </section>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "32px" }}>
          <div>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>What Is a CP503?</h2>
            <p style={{ fontSize: "15px", color: "rgba(255,255,255,0.75)", lineHeight: "1.7", marginBottom: "24px" }}>CP503 is the third notice in the IRS collection sequence. By the time you receive it, penalties and interest have already been accruing for weeks. The IRS is signaling that enforcement action is approaching.</p>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "16px" }}>What to Do Now</h2>
            <ol style={{ paddingLeft: "20px", color: "rgba(255,255,255,0.75)", fontSize: "15px" }}>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Do not ignore this notice — CP504 follows within 30 days</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>If you can pay, pay the full balance immediately to stop escalation</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>If you cannot pay, contact a tax professional TODAY to set up an installment agreement or explore other options</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Check your IRS account online at irs.gov/account to see your full balance</li>
            </ol>
          </div>
          <div>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>Where You Are in the Process</h2>
            <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>✓ CP14 (sent)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>✓ CP501 (sent)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#D4A843", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "#fff", fontWeight: "600" }}>CP503 (YOU ARE HERE)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>CP504 — Final Notice (30 days)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Federal Tax Lien Filed</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Bank Levy / Wage Garnishment</span></div>
            </div>
          </div>
        </section>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}>
          <h2 style={{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}>Common Questions About CP503</h2>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>How serious is a CP503 notice?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Very serious. CP503 means the IRS has already sent two previous notices and is escalating toward enforcement. CP504 — which authorizes bank levies and wage garnishments — typically follows within 30 days.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Can I still set up a payment plan after CP503?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes — but you need to act immediately. An installment agreement must be set up before CP504 is issued to prevent levy action. A tax professional can often have an agreement in place within 24-48 hours.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>What's the difference between CP503 and a tax lien?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>CP503 is a notice — it has no legal effect on your property yet. A federal tax lien is a legal claim filed with the county clerk that appears on public record. If you receive CP504 and don't respond, a lien is typically the next step.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Will the IRS negotiate at the CP503 stage?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes — and they often prefer it. The IRS would rather set up a payment arrangement than pursue costly enforcement. However, you must proactively contact them or have a professional do so before CP504 is issued.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>I can't afford to pay — what are my options at CP503?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Options include: installment agreement (pay over time), offer in compromise (settle for less if you qualify), currently not collectible status (pause collection due to hardship), or penalty abatement (reduce what you owe). A tax professional can identify which applies to your situation.</p>
            </div>
        </section>

        <section style={{ background: "rgba(212,168,67,0.1)", padding: "60px 24px", textAlign: "center" }}>
          <h2 style={{ fontSize: "28px", fontWeight: "700", marginBottom: "16px" }}>Get Expert Help With Your CP503 Notice</h2>
          <p style={{ color: "rgba(255,255,255,0.7)", marginBottom: "32px", maxWidth: "480px", margin: "0 auto 32px" }}>
            Licensed tax professionals · 48-hour response · $399 case review
          </p>
          <Link href="/#quiz" style={{ background: "#D4A843", color: "#0F1B2D", padding: "16px 36px", borderRadius: "4px", fontWeight: "700", fontSize: "16px", textDecoration: "none", display: "inline-block" }}>
            Start Free Assessment →
          </Link>
        </section>
      </main>
    </>
  )
}
