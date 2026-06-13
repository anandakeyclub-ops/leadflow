import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Alachua County IRS Tax Lien Help | TaxCase Review Florida",
  description: "IRS tax lien filed in Alachua County? Licensed tax professionals help Gainesville residents resolve federal tax liens. $399 case review. Call (561) 247-0678.",
  keywords: "Alachua IRS tax lien, Gainesville tax debt help, Alachua County tax lien removal, IRS lien Gainesville Florida",
  openGraph: {
    title: "Alachua County IRS Tax Lien Help | TaxCase Review",
    description: "Licensed tax professionals helping Alachua County residents resolve IRS tax liens. $399 comprehensive case review.",
    url: "https://taxcasereview.org/florida/alachua/irs-tax-lien-help",
  },
}

const schema = {
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "TaxCase Review — Alachua County",
  "url": "https://taxcasereview.org/florida/alachua/irs-tax-lien-help",
  "telephone": "+15612470678",
  "areaServed": {
    "@type": "AdministrativeArea",
    "name": "Alachua County, Florida"
  },
  "serviceType": "IRS Tax Lien Resolution",
  "priceRange": "$399",
}

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "How do I resolve an IRS tax lien in Alachua County?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "There are several paths to resolve an IRS tax lien in Alachua County including installment agreements, offer in compromise, lien withdrawal, and penalty abatement. The best option depends on your specific financial situation. A licensed tax professional can review your case and identify which programs you qualify for."
      }
    },
    {
      "@type": "Question",
      "name": "Can I remove an IRS tax lien from my Alachua County property?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes — an IRS tax lien can be removed from your Alachua County property through a Certificate of Discharge (for specific property), lien withdrawal after full payment or installment agreement, or lien subordination to allow refinancing. Each option has different eligibility requirements."
      }
    },
    {
      "@type": "Question",
      "name": "How much does it cost to resolve an IRS lien in Gainesville?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "TaxCase Review offers a comprehensive $399 case review that covers a full analysis of your IRS tax situation, review of all available resolution options, and a personalized action plan. This is the most cost-effective first step for Gainesville area taxpayers with IRS lien issues."
      }
    },
    {
      "@type": "Question",
      "name": "Does an IRS tax lien affect my credit in Alachua County?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "The IRS no longer reports tax liens to credit bureaus directly (since 2018). However, federal tax liens are public record in Alachua County and can appear in public record searches used by lenders, title companies, and employers. Resolving the lien removes it from public record."
      }
    },
  ]
}

export default function AlachuaPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />

      <main style={{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff" }}>

        {/* Breadcrumb */}
        <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.1)", fontSize: "13px", color: "rgba(255,255,255,0.5)" }}>
          <Link href="/" style={{ color: "#D4A843", textDecoration: "none" }}>Home</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <Link href="/florida" style={{ color: "#D4A843", textDecoration: "none" }}>Florida</Link>
          <span style={{ margin: "0 8px" }}>›</span>
          <span>Alachua County</span>
        </div>

        {/* Hero */}
        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "60px 24px 48px" }}>
          <div style={{ fontSize: "12px", color: "#D4A843", letterSpacing: "0.15em", textTransform: "uppercase", marginBottom: "16px" }}>
            Alachua County · Florida
          </div>
          <h1 style={{ fontSize: "clamp(28px, 5vw, 42px)", fontWeight: "700", lineHeight: "1.2", marginBottom: "20px" }}>
            IRS Tax Lien Filed in Alachua County?
          </h1>
          <p style={{ fontSize: "18px", color: "rgba(255,255,255,0.75)", lineHeight: "1.7", marginBottom: "32px" }}>
            Federal tax liens are public record in Alachua County. They affect your property, credit, and ability to sell or refinance. 
            Our licensed tax professionals have reviewed hundreds of tax lien cases in the Gainesville area — we know your options.
          </p>
          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
            <Link
              href="/#quiz"
              style={{ background: "#D4A843", color: "#0F1B2D", padding: "14px 28px", borderRadius: "4px", fontWeight: "700", fontSize: "15px", textDecoration: "none", display: "inline-block" }}
            >
              See My Options — 60 Seconds
            </Link>
            <a
              href="tel:+15612470678"
              style={{ border: "1px solid rgba(255,255,255,0.3)", color: "#fff", padding: "14px 24px", borderRadius: "4px", fontSize: "15px", textDecoration: "none", display: "inline-block" }}
            >
              (561) 247-0678
            </a>
          </div>
        </section>

        {/* Direct answer block — optimized for AI citations */}
        <section style={{ background: "rgba(212,168,67,0.08)", borderLeft: "4px solid #D4A843", margin: "0 24px 48px", padding: "24px 28px", borderRadius: "0 8px 8px 0", maxWidth: "752px", marginLeft: "auto", marginRight: "auto" }}>
          <p style={{ fontSize: "15px", lineHeight: "1.7", color: "rgba(255,255,255,0.85)" }}>
            <strong style={{ color: "#D4A843" }}>Quick answer:</strong> An IRS tax lien in Alachua County can be resolved through an 
            installment agreement, offer in compromise, lien withdrawal, penalty abatement, or currently not collectible status. 
            The right path depends on your income, assets, and how much you owe. A $399 case review identifies your exact options within 48 hours.
          </p>
        </section>

        {/* Resolution options */}
        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}>
          <h2 style={{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}>
            Resolution Options Available in Alachua County
          </h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px" }}>
            {[
              { title: "Installment Agreement", desc: "Pay your tax debt in manageable monthly payments over time." },
              { title: "Offer in Compromise", desc: "Settle your IRS debt for less than the full amount owed if you qualify." },
              { title: "Lien Withdrawal", desc: "Remove the lien from public record after qualifying payment arrangement." },
              { title: "Penalty Abatement", desc: "Reduce or eliminate penalties and interest based on reasonable cause." },
              { title: "Currently Not Collectible", desc: "Temporarily pause IRS collection if you're facing financial hardship." },
              { title: "Innocent Spouse Relief", desc: "Protection from your spouse or ex-spouse's tax liability in some cases." },
            ].map((opt) => (
              <div key={opt.title} style={{ background: "rgba(255,255,255,0.05)", border: "0.5px solid rgba(255,255,255,0.1)", borderRadius: "8px", padding: "20px" }}>
                <h3 style={{ fontSize: "15px", fontWeight: "600", color: "#D4A843", marginBottom: "8px" }}>{opt.title}</h3>
                <p style={{ fontSize: "13px", color: "rgba(255,255,255,0.65)", lineHeight: "1.6" }}>{opt.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* FAQ */}
        <section style={{ maxWidth: "800px", margin: "0 auto", padding: "0 24px 60px" }}>
          <h2 style={{ fontSize: "24px", fontWeight: "700", marginBottom: "32px" }}>
            Alachua County IRS Tax Lien — Frequently Asked Questions
          </h2>
          {[
            {
              q: `How do I resolve an IRS tax lien in Alachua County?`,
              a: `There are several paths: installment agreements, offer in compromise, lien withdrawal, and penalty abatement. The best option depends on your specific financial situation. A licensed tax professional can identify which programs you qualify for.`
            },
            {
              q: `Can I remove an IRS tax lien from my Alachua County property?`,
              a: `Yes — through a Certificate of Discharge (for specific property), lien withdrawal after payment or installment agreement, or lien subordination to allow refinancing. Each has different eligibility requirements.`
            },
            {
              q: `How much does it cost to resolve an IRS lien in Gainesville?`,
              a: `TaxCase Review offers a $399 comprehensive case review that covers a full analysis of your situation, all available resolution options, and a personalized action plan. This is the most cost-effective first step.`
            },
            {
              q: `Does an IRS tax lien affect my credit in Alachua County?`,
              a: `The IRS no longer reports tax liens to credit bureaus directly (since 2018). However, federal tax liens are public record in Alachua County and can appear in searches used by lenders and title companies.`
            },
          ].map((item, i) => (
            <div key={i} style={{ borderBottom: "0.5px solid rgba(255,255,255,0.1)", paddingBottom: "24px", marginBottom: "24px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "10px", color: "#fff" }}>{item.q}</h3>
              <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", lineHeight: "1.7" }}>{item.a}</p>
            </div>
          ))}
        </section>

        {/* CTA */}
        <section style={{ background: "rgba(212,168,67,0.1)", padding: "60px 24px", textAlign: "center" }}>
          <h2 style={{ fontSize: "28px", fontWeight: "700", marginBottom: "16px" }}>
            Get Your Alachua County Case Reviewed
          </h2>
          <p style={{ color: "rgba(255,255,255,0.7)", marginBottom: "32px", maxWidth: "480px", margin: "0 auto 32px" }}>
            8 questions. 60 seconds. A licensed tax professional reviews your case and outlines your exact options.
          </p>
          <Link
            href="/#quiz"
            style={{ background: "#D4A843", color: "#0F1B2D", padding: "16px 36px", borderRadius: "4px", fontWeight: "700", fontSize: "16px", textDecoration: "none", display: "inline-block" }}
          >
            Start Free Assessment →
          </Link>
          <p style={{ marginTop: "16px", fontSize: "13px", color: "rgba(255,255,255,0.4)" }}>
            $399 · Licensed Professionals · 100% Confidential
          </p>
        </section>

      </main>
    </>
  )
}
