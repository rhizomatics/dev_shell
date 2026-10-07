# The Object Tree

All of the objects (only entities for now) are arranged in a giant tree, like a file system, exposed as the global variable `obj` and implemented as Python [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping) object (which provides the [MappingView](https://docs.python.org/3/library/collections.abc.html#collections.abc.MappingView) views [ItemsView](https://docs.python.org/3/library/collections.abc.html#collections.abc.ItemsView) and [KeysView](https://docs.python.org/3/library/collections.abc.html#collections.abc.KeysView))

`obj` offers:

- Dictionary style access, using `[]`
  - Raises `KeyError` if entity or sub-path doesn't exist
  - Each level of the tree returns a sub-tree
  - `keys()`,`values()`,`items()` of the sub-tree shows only that level
  - An `OrderedView` is used rather than plain `MappingView` so can be accessed like a `list` and items are alphabetically organized
- `find()`
  - Returns a flat iterable of the entire tree
  - Optionally restrict by `platform`,`area`,`label`
- `show()`
  - Dump the most useful info on an object to console

All the usual Python tricks can of course also be used, iterators, comprehensions, classes, lambdas or a simple `len()`.

### `obj[]`

This allows dictionary ('Mapping') access to the object tree. It can also accept a simple entity name and bypass the tree structure altogether.

In the example tree below, the objects and subtrees of objects can be accessed like:

```python
>>> obj["/rflink/sensor/shed_temperature"].state  # prints out temperature
>>> obj[
    "/rflink/sensor/shed_temperature"
].state_attributes  # prints out additional attributes
>>> obj["/rflink"]  # the 'light','binary_sensor' and 'sensor' subtrees for rflink
>>> obj["/rflink/sensor"]  # all the sensors for rflink
>>> len(obj["/rflink/sensor"])  # count of rflink sensors in this example
>>> obj["sensor.shed_temperature"] # non-path simple entity name mode
```

##### Example Tree

```
...
- mqtt
- rflink
  - light
    - staircase_ceiling
    - shed
  - switch
    - upstairs_pixie
  - binary_sensor
    - porch_pir
    - shed_door
  - sensor
    - shed_temperature
    - kitchen_humidity
...
```

!!! note
    In the roadmap, there will be a visual Object Browser to view and select entities. For now, it is accessible only via Python code. It also may extend beyond entities, to things like areas, users, categories and devices.

#### `obj.find('..')`

Where the dictionary access gives a nested directory view of the object tree, `find` provides a flat iteration with no order guarantees, so its fast and simple and can be sorted the usual Python way if needed.

`find` functions can accept a full or partial path, or a regular expression.

`find` also has built in filters, to narrow the big list of objects by one or more `platform`,`domain`,`area`,`label` - each of these will take a single string or list of strings, and they can be combined to narrow down the list.

```python
>>> {(o.entity_id, o.state) for o in obj.find(domain="binary_sensor")}
```

Or using a regular expression:

```python
>>> {(o.entity_id, o.state) for o in obj.find("/rflink/binary_sensor/*._leak")}
```

##### Raw Objects

In API Client mode, `find()` returns a local proxy for the remote class, normalized to look more like the same object you'd get in live mode. Switching `raw=True` will bypass this and you'll get the object untouched as it was received from the API.

#### `obj.find_paths(..)`

Identical to `obj.find()` except it only returns an iterable of the object paths rather than the objects themselves. Ideal for plugging into some logic that will then call `obj[path]` on each one.

```python
>>> list(obj.find(area="kitchen"))  # list names of all entities in kitchen
>>> sorted(obj.find(area=["kitchen", "shed"]))
>>> list(obj.find(".*_leak"))
```

#### `obj.find_names(..)`

Same as `obj.find_paths()` except it returns the object bare name, as it would appear in Home Assistant, e.g. `sensor.bathroom_humidity`

#### `obj.show(..)`

The `show` function will give a pretty version of an object where it knows how. For entities this means it combines the `RegistryEntry` and `Entity` information, drops some boring internal stuff, filters out all the `None` values and turns `datetime.datetime` structures into local date times.

```yaml
obj.show('/unifi/sensor/kitchen_wifi_cpu_utilization')
```