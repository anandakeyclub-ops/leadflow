import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "IRS Tax Lien Help by County | Florida | TaxCase Review",
  description: "Licensed IRS tax lien resolution for all Florida counties. Find your county for local information and case review options.",
}

export default function FloridaPage() {
  return (
    <main style={{ fontFamily: "Georgia, serif", background: "#0F1B2D", minHeight: "100vh", color: "#fff", padding: "60px 24px" }}>
      <div style={{ maxWidth: "800px", margin: "0 auto" }}>
        <h1 style={{ fontSize: "36px", fontWeight: "700", marginBottom: "16px" }}>
          IRS Tax Lien Help — Florida Counties
        </h1>
        <p style={{ color: "rgba(255,255,255,0.7)", marginBottom: "48px", fontSize: "17px" }}>
          Select your county for local IRS tax lien information and resolution options.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px" }}>
          <Link href="/florida/miami-dade/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Miami-Dade County</Link>
          <Link href="/florida/broward/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Broward County</Link>
          <Link href="/florida/palm-beach/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Palm Beach County</Link>
          <Link href="/florida/hillsborough/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Hillsborough County</Link>
          <Link href="/florida/pinellas/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Pinellas County</Link>
          <Link href="/florida/orange/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Orange County</Link>
          <Link href="/florida/duval/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Duval County</Link>
          <Link href="/florida/lee/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Lee County</Link>
          <Link href="/florida/polk/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Polk County</Link>
          <Link href="/florida/sarasota/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Sarasota County</Link>
          <Link href="/florida/manatee/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Manatee County</Link>
          <Link href="/florida/martin/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Martin County</Link>
          <Link href="/florida/lake/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Lake County</Link>
          <Link href="/florida/pasco/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Pasco County</Link>
          <Link href="/florida/osceola/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Osceola County</Link>
          <Link href="/florida/seminole/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Seminole County</Link>
          <Link href="/florida/collier/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Collier County</Link>
          <Link href="/florida/brevard/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Brevard County</Link>
          <Link href="/florida/volusia/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Volusia County</Link>
          <Link href="/florida/st-johns/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ St. Johns County</Link>
          <Link href="/florida/alachua/irs-tax-lien-help" style={{ color: "#D4A843", textDecoration: "none", fontSize: "15px" }}>→ Alachua County</Link>
        </div>
      </div>
    </main>
  )
}
