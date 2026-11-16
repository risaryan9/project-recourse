import HealthStatus from "./health-status";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-8 bg-zinc-50 px-6 py-16 dark:bg-black">
      <header className="max-w-md text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Recourse</h1>
        <p className="mt-2 text-zinc-600 dark:text-zinc-400">
          Recover money your bank owes you after a failed or fraudulent digital payment.
        </p>
      </header>
      <HealthStatus />
      <p className="max-w-md text-center text-xs text-zinc-500">
        Recourse provides information, not legal advice.
      </p>
    </main>
  );
}
