import HealthStatus from "./health-status";

export const metadata = { title: "System status · Recourse" };

export default function StatusPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-8 px-6 py-16">
      <h1 className="type-heading">System status</h1>
      <HealthStatus />
    </main>
  );
}
