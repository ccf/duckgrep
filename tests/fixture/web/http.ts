export async function fetchJson(url: string) {
  const r = await fetch(url);
  return r.json();
}
