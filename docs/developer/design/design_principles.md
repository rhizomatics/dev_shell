1. Safe-ish by default, full power for those who can handle it
2. No pickling unless unavoidable
   - SQL results are sent back to client as `feather` Apache Arrow dataframes
   - Home Assistant API calls as REST API and interpreted on client
   - `hass` needs client side objects serialized when arguments, and server-side responses
3. Maximum freedom for the developer on the client, maximum restrictions on the server side
   - No limits on memory, CPU, package loading, (within reason) Python versions on the client side REPL. 
   - Server side code is all regrettable, every dependency brings some extra risk to the running instance