import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "IRS CP14 Notice — What It Means and What to Do | TaxCase Review Florida",
  description: "Received an IRS CP14 notice? This is the first formal demand for payment. Learn what CP14 means, your options, and how to respond. Florida tax professionals available.",
}

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    { "@type": "Question", "name": "What happens if I ignore a CP14 notice?", "acceptedAnswer": { "@type": "Answer", "text": "If ignored, the IRS issues a CP501 within 5 weeks, then CP503, then CP504 (Final Notice Before Levy). After CP504, the IRS can file a federal tax lien and begin collection action including bank levies and wage garnishments." } },
    { "@type": "Question", "name": "Is a CP14 the same as a tax lien?", "acceptedAnswer": { "@type": "Answer", "text": "No. A CP14 is a payment demand notice. A federal tax lien is filed separately and appears on public record. However, ignoring a CP14 is one of the fastest paths to having a lien filed against you." } },
    { "@type": "Question", "name": "Can I dispute a CP14 notice?", "acceptedAnswer": { "@type": "Answer", "text": "Yes. If you believe the amount is incorrect, you can respond in writing with supporting documentation. Common errors include payments not credited, incorrect filing status, or math errors on your return." } },
    { "@type": "Question", "name": "How long do I have to respond to a CP14?", "acceptedAnswer": { "@type": "Answer", "text": "The due date is printed on the notice — typically 21 days from the notice date. However, you should respond within 60 days to preserve your appeal rights if you disagree with the balance." } },
    { "@type": "Question", "name": "Can I set up a payment plan after a CP14?", "acceptedAnswer": { "@type": "Answer", "text": "Yes — and this is often the best option for people who cannot pay in full. The IRS offers installment agreements for balances under $50,000 that can be set up online. A tax professional can often negotiate better terms." } },
  ]
}

export default function CP14Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }} />
      <main style={{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff" }}>

        <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.1)", fontSize: "13px", color: "rgba(255,255,255,0.5)" }}>
          <Link href="/" style={{ color: "#D4A843", textDecoration: "none" }}>Home</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <Link href="/irs-notices" style={{ color: "#D4A843", textDecoration: "none" }}>IRS Notices</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <span>CP14</span>
        </div>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "60px 24px 48px" }}>
          <div style={{ display: "inline-block", background: "#D4A84322", border: "1px solid #D4A843", color: "#D4A843", fontSize: "12px", fontWeight: "700", letterSpacing: "0.1em", padding: "4px 12px", borderRadius: "4px", marginBottom: "20px", textTransform: "uppercase" }}>
            Moderate — first notice, but ignoring it starts the clock on penalties and escalation.
          </div>
          <h1 style={{ fontSize: "clamp(26px, 4vw, 38px)", fontWeight: "700", lineHeight: "1.2", marginBottom: "24px" }}>
            Received an IRS CP14 Notice? Here's Exactly What It Means.
          </h1>
          <div style={{ background: "rgba(212,168,67,0.08)", borderLeft: "4px solid #D4A843", padding: "20px 24px", borderRadius: "0 8px 8px 0", marginBottom: "40px" }}>
            <p style={{ fontSize: "15px", lineHeight: "1.7", color: "rgba(255,255,255,0.85)", margin: 0 }}>
              <strong style={{ color: "#D4A843" }}>What this means: </strong>A CP14 notice means the IRS believes you owe taxes and this is their first formal request for payment. It is not a lien or a levy — but it is the first step in an escalation process that can lead to both. You have approximately 60 days to respond before the next notice is issued.
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
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>What Is a CP14?</h2>
            <p style={{ fontSize: "15px", color: "rgba(255,255,255,0.75)", lineHeight: "1.7", marginBottom: "24px" }}>CP14 is the IRS's first formal balance-due notice. It's generated automatically when your tax return shows a balance and the IRS has not received payment. It includes the amount owed, the tax year, and a due date — typically 21 days from the notice date.</p>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "16px" }}>What to Do Now</h2>
            <ol style={{ paddingLeft: "20px", color: "rgba(255,255,255,0.75)", fontSize: "15px" }}>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Verify the amount is correct by reviewing your return and IRS records</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Determine if you can pay in full, need a payment plan, or qualify for other relief</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Respond within 60 days — either with payment or a written response</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Do NOT ignore this notice — silence triggers CP501 within 5 weeks</li>
            </ol>
          </div>
          <div>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>Where You Are in the Process</h2>
            <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>CP14</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>CP501 (5 weeks)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>CP503 (5 weeks)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>CP504 — Final Notice (30 days)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Federal Tax Lien Filed</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Bank Levy / Wage Garnishment</span></div>
            </div>
          </div>
        </section>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}>
          <h2 style={{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}>Common Questions About CP14</h2>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>What happens if I ignore a CP14 notice?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>If ignored, the IRS issues a CP501 within 5 weeks, then CP503, then CP504 (Final Notice Before Levy). After CP504, the IRS can file a federal tax lien and begin collection action including bank levies and wage garnishments.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Is a CP14 the same as a tax lien?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>No. A CP14 is a payment demand notice. A federal tax lien is filed separately and appears on public record. However, ignoring a CP14 is one of the fastest paths to having a lien filed against you.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Can I dispute a CP14 notice?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes. If you believe the amount is incorrect, you can respond in writing with supporting documentation. Common errors include payments not credited, incorrect filing status, or math errors on your return.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>How long do I have to respond to a CP14?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>The due date is printed on the notice — typically 21 days from the notice date. However, you should respond within 60 days to preserve your appeal rights if you disagree with the balance.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Can I set up a payment plan after a CP14?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes — and this is often the best option for people who cannot pay in full. The IRS offers installment agreements for balances under $50,000 that can be set up online. A tax professional can often negotiate better terms.</p>
            </div>
        </section>

        <section style={{ background: "rgba(212,168,67,0.1)", padding: "60px 24px", textAlign: "center" }}>
          <h2 style={{ fontSize: "28px", fontWeight: "700", marginBottom: "16px" }}>Get Expert Help With Your CP14 Notice</h2>
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
