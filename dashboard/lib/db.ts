import { Pool, types, type QueryResultRow } from "pg";

// node-postgres returns NUMERIC/DECIMAL columns as strings by default (to
// avoid silent precision loss on values too large for a JS number). Every
// price/qty/P&L column in db/schema.sql is NUMERIC, and most pages/components
// treat them as numbers (.toFixed(), arithmetic, chart data) - without this,
// real rows throw a client-side render error the moment they exist (this was
// invisible before because the affected pages only ever hit their "no data
// yet" branch). OID 1700 = numeric. Parsing to float here, once, is simpler
// and safer than adding Number(...) at every one of the many call sites.
types.setTypeParser(1700, (val: string) => (val === null ? null : parseFloat(val)));

// Single pooled connection reused across requests (Next.js keeps this module
// warm between invocations on the same server instance). Reads only - the
// dashboard never writes to the bot's tables.
declare global {
  // eslint-disable-next-line no-var
  var _pgPool: Pool | undefined;
}

function getPool(): Pool {
  if (!process.env.DATABASE_URL) {
    throw new Error(
      "DATABASE_URL is not set. Add the Postgres plugin in Railway (or set " +
        "DATABASE_URL locally) - see dashboard/README.md."
    );
  }
  if (!global._pgPool) {
    global._pgPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: 5,
      idleTimeoutMillis: 30_000,
      ssl: process.env.DATABASE_URL.includes("localhost")
        ? undefined
        : { rejectUnauthorized: false },
    });
  }
  return global._pgPool;
}

export async function query<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params: unknown[] = []
): Promise<T[]> {
  const pool = getPool();
  const result = await pool.query<T>(text, params);
  return result.rows;
}

export async function queryOne<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params: unknown[] = []
): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] ?? null;
}
