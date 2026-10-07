1. Safe-ish by default, full power for those who can handle it
1. No pickling unless unavoidable
   - SQL results are sent back to client as `feather` Apache Arrow dataframes
   - Home Assistant API calls as REST API and interpreted on client
   - `hass` needs client side objects serialized when arguments, and server-side responses
1. Maximum freedom for the developer on the client, maximum restrictions on the server side
   - No limits on memory, CPU, package loading, (within reason) Python versions on the client side REPL.
   - Server side code is all regrettable, every dependency brings some extra risk to the running instance
1. Agents are first-class users. They can benefit from real world feedback of code and ideas, so live mode is likely to be the most useful, and also the most dangerous unless restricted to devcontainer type environments.
   - In commercial environments, cloning - ideally CoW or zero-copy style - of production environments to development ones is a common way to get realistic or even repro environments accessible safely.
