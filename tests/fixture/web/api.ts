import { fetchJson as get } from "./http";
import * as http from "./http";

/** A client. */
export class Client {
  constructor(private base: string) {}
  async user(id: number): Promise<User> {
    return get(this.base + "/u/" + id);
  }
  ping() { return http.fetchJson("/ping"); }
}

export const makeClient = (base: string): Client => new Client(base);

interface User { id: number }
