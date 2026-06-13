import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "IRS CP504 Notice — Final Warning Before Levy | Act Now | TaxCase Review Florida",
  description: "CP504 is the IRS Final Notice Before Levy. Bank accounts and wages can be seized within 30 days. Florida tax professionals available for urgent cases. Call (561) 247-0678.",
}

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    { "@type": "Question", "name": "How much time do I have after a CP504?", "acceptedAnswer": { "@type": "Answer", "text": "The IRS can seize your state tax refund immediately. Bank levies and wage garnishments can begin after 30 days. However, you can pause this by requesting a Collection Due Process (CDP) hearing within 30 days of the CP504 date." } },
    { "@type": "Question", "name": "Can a tax lien already be filed after CP504?", "acceptedAnswer": { "@type": "Answer", "text": "Yes — the IRS often files a federal tax lien around the same time as CP504. This appears on public record and affects your credit, property, and ability to sell or refinance." } },
    { "@type": "Question", "name": "What is a Collection Due Process hearing?", "acceptedAnswer": { "@type": "Answer", "text": "A CDP hearing is your legal right to appeal IRS collection action. Requesting one within 30 days of CP504 pauses the levy while your case is reviewed. A tax professional can file this request and negotiate on your behalf." } },
    { "@type": "Question", "name": "Will the IRS still accept a payment plan at CP504?", "acceptedAnswer": { "@type": "Answer", "text": "Yes — but you must act immediately. The IRS generally prefers a payment arrangement over costly enforcement. An experienced tax professional can often halt levy action within 24-48 hours by establishing an installment agreement." } },
    { "@type": "Question", "name": "I received CP504 and I'm terrified — what should I do right now?", "acceptedAnswer": { "@type": "Answer", "text": "Call us directly at (561) 247-0678. We handle CP504 cases regularly and can often halt levy action quickly. The worst thing you can do is wait. Our $399 case review includes same-week response for CP504 situations." } },
  ]
}

export default function CP504Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }} />
      <main style={{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff" }}>

        <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.1)", fontSize: "13px", color: "rgba(255,255,255,0.5)" }}>
          <Link href="/" style={{ color: "#D4A843", textDecoration: "none" }}>Home</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <Link href="/irs-notices" style={{ color: "#D4A843", textDecoration: "none" }}>IRS Notices</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <span>CP504</span>
        </div>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "60px 24px 48px" }}>
          <div style={{ display: "inline-block", background: "#E8404022", border: "1px solid #E84040", color: "#E84040", fontSize: "12px", fontWeight: "700", letterSpacing: "0.1em", padding: "4px 12px", borderRadius: "4px", marginBottom: "20px", textTransform: "uppercase" }}>
            CRITICAL — levy authority is active. Contact a tax professional today.
          </div>
          <h1 style={{ fontSize: "clamp(26px, 4vw, 38px)", fontWeight: "700", lineHeight: "1.2", marginBottom: "24px" }}>
            IRS CP504 Notice — This Is a Final Warning. Here's What Happens Next.
          </h1>
          <div style={{ background: "rgba(212,168,67,0.08)", borderLeft: "4px solid #D4A843", padding: "20px 24px", borderRadius: "0 8px 8px 0", marginBottom: "40px" }}>
            <p style={{ fontSize: "15px", lineHeight: "1.7", color: "rgba(255,255,255,0.85)", margin: 0 }}>
              <strong style={{ color: "#D4A843" }}>What this means: </strong>A CP504 notice is the IRS's Final Notice of Intent to Levy. This means the IRS now has legal authority to seize your state tax refund immediately and can levy your bank accounts and garnish your wages within 30 days. This is the most urgent IRS notice — action within 24-48 hours is critical.
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
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>What Is a CP504?</h2>
            <p style={{ fontSize: "15px", color: "rgba(255,255,255,0.75)", lineHeight: "1.7", marginBottom: "24px" }}>CP504 is the fourth and final notice in the standard IRS collection sequence. Upon issuing CP504, the IRS can immediately seize your state tax refund. After 30 days, they can levy bank accounts, garnish wages, and seize other assets. A federal tax lien is typically filed around this time as well.</p>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "16px" }}>What to Do Now</h2>
            <ol style={{ paddingLeft: "20px", color: "rgba(255,255,255,0.75)", fontSize: "15px" }}>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Call a tax professional TODAY — (561) 247-0678 (urgent cases prioritized)</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Request a Collection Due Process hearing within 30 days to pause levy action</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Do NOT withdraw money from bank accounts — this can trigger faster action</li>
              <li style={{ marginBottom: "12px", lineHeight: "1.6" }}>Gather your most recent tax returns, IRS notices, and financial documents</li>
            </ol>
          </div>
          <div>
            <h2 style={{ fontSize: "20px", fontWeight: "700", marginBottom: "20px" }}>Where You Are in the Process</h2>
            <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: "8px", padding: "20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>✓ CP14 (sent)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>✓ CP501 (sent)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>✓ CP503 (sent)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#D4A843", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "#fff", fontWeight: "600" }}>CP504 — YOU ARE HERE (levy authority active)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Bank Levy / Wage Garnishment (30 days)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}><span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255,255,255,0.2)", flexShrink: "0" }}></span><span style={{ fontSize: "14px", color: "rgba(255,255,255,0.5)", fontWeight: "400" }}>Federal Tax Lien Filed (may already be filed)</span></div>
            </div>
          </div>
        </section>

        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}>
          <h2 style={{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}>Common Questions About CP504</h2>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>How much time do I have after a CP504?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>The IRS can seize your state tax refund immediately. Bank levies and wage garnishments can begin after 30 days. However, you can pause this by requesting a Collection Due Process (CDP) hearing within 30 days of the CP504 date.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Can a tax lien already be filed after CP504?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes — the IRS often files a federal tax lien around the same time as CP504. This appears on public record and affects your credit, property, and ability to sell or refinance.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>What is a Collection Due Process hearing?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>A CDP hearing is your legal right to appeal IRS collection action. Requesting one within 30 days of CP504 pauses the levy while your case is reviewed. A tax professional can file this request and negotiate on your behalf.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>Will the IRS still accept a payment plan at CP504?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Yes — but you must act immediately. The IRS generally prefers a payment arrangement over costly enforcement. An experienced tax professional can often halt levy action within 24-48 hours by establishing an installment agreement.</p>
            </div>
            <div style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>I received CP504 and I'm terrified — what should I do right now?</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>Call us directly at (561) 247-0678. We handle CP504 cases regularly and can often halt levy action quickly. The worst thing you can do is wait. Our $399 case review includes same-week response for CP504 situations.</p>
            </div>
        </section>

        <section style={{ background: "rgba(212,168,67,0.1)", padding: "60px 24px", textAlign: "center" }}>
          <h2 style={{ fontSize: "28px", fontWeight: "700", marginBottom: "16px" }}>Get Expert Help With Your CP504 Notice</h2>
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
