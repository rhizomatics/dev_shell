# Examples

These showcase simple examples of using basic Python object notation, along with comprenhensions, to get command line access to Home Assistant APIs and data.

## API Mode

!!! note
    These examples don't need anything installed on the Home Assistant server - all you need is the Long Lived Access Token.

### Object Tree

#### Inspect an entity
```python
obj["/mqtt/sensor/greenhouse_temperature"]  # by object tree path
obj["sensor.greenhouse_temperature"]  # by HA entity_id
```

#### Find objects

Get an iterable of entity objects by reg exp on object tree and do something with them

```python
[o.area_id for o in obj.find(".*greenhouse.*")]
```

Add a filter, and return a tuple of values, in this case listing all the entities that seem to be in the greenhouse that don't have the right area assigned

```python
[
    (o.entity_id, o.area_id)
    for o in obj.find(".*greenhouse.*")
    if o.area_id != "greenhouse"
]
```

#### Find object names

```python
[n for n in obj.find_names(platform="mqtt" domain="binary_sensor") if 'detector' in n and 'test' not in n]
```

Also possible to use regular expressions to find things, which can be combined with the `platform` etc filters

```python
obj.find_names(".*(alarm|detector).*_test$")
```
### Entity and Entity Registry inspection

Find me all the MQTT devices that are exposed to Alexa.

!!! note
    `get("cloud.alexa",{})` used rather than `["cloud_alexa"]` since not all entities will have this structure.

```python
[
    o.entity_id
    for o in obj.find(platform="mqtt")
    if o.registry["options"].get("cloud.alexa", {}).get("should_expose", {})
]
```

### `homeassistant-api` integration

#### Traverse the objects

```python
hass_api.get_domain("switch").services.keys()
```

!!! tip
    More examples for `hass_api` at the [homeassistant-api](https://homeassistantapi.readthedocs.io/en/stable/usage.html#services) docs.

## Live Mode

!!! note
    These examples need the [Live Server](live_mode.md#install-local-home-assistant-with-the-live-server) HACS component installed on a Home Assistant server.

### `hass` object

#### Simple entity fetch

```python
hass.states.get("sun.sun")  # returns a `State` object
hass.states.get("sun.sun").state  # returns sun position as string
hass.states.get("sun.sun").attributes["next_dawn"]  # date time from attributes
```

#### Get integration data

Note that each integration can have wildly different data, and some like `mqtt` can be huge.

```python
hass.data["notify"].entities
```

#### Mapping of all notify entities by platform name
```python
{n.name: n.platform.platform_name for n in hass.data["notify"].entities}
```


### SQL

#### Get a list of tables and their columns

```python
sql.tables
```

#### Quick analysis of tables

```python
sql("select count(*) from statistics").show()
```

#### Distinct field analysis 

- `[0][0]` notation here means get me the value of the first column of the first row. 
- Dictionary comprehension to do all the analysis and build report in one line
- `f` string to build a simple query
- Could extend this to `legacy` columns by passing `legacy=true` on the `sql` and `sql.table` calls

```python
 { col:sql(f'select count(distinct {col}) from events')[0][0] for col
in sql.table("events").column_names }
{
    'event_id': 173121,
    'origin_idx': 2,
    'time_fired_ts': 173112,
    'data_id': 97248,
    'context_id_bin': 161680,
    'context_user_id_bin': 4,
    'context_parent_id_bin': 1679,
    'event_type_id': 42
}
```

!!! tip
    For more advanced data analysis, use `polars` or `pandas`
    and turn the `sql` result into a dataframe using `to_polars()` or `to_pandas()`
