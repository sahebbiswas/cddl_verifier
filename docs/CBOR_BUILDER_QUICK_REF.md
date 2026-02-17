# CBOR Quick Reference - Iterative Construction

## Cheat Sheet

### Create CBOR Object

```python
from simple_cbor import CBOR

cbor = CBOR({})              # Empty dict
cbor = CBOR([])              # Empty list  
cbor = CBOR({"a": 1})        # From dict
cbor = CBOR.load(cbor_bytes) # From bytes
```

### Builder Methods (Fluent API)

| Method | Use Case | Example |
|--------|----------|---------|
| `.set(key, val)` | Add/update single field | `cbor.set("name", "Alice")` |
| `.update(dict)` | Add/update multiple fields | `cbor.update({"a": 1, "b": 2})` |
| `.delete(key)` | Remove field | `cbor.delete("old_field")` |
| `.append(val)` | Add to array | `cbor.append(42)` |
| `.extend(vals)` | Add multiple to array | `cbor.extend([1, 2, 3])` |
| `.clear()` | Empty data | `cbor.clear()` |
| `.copy()` | Deep copy | `cbor2 = cbor.copy()` |
| `.merge(other)` | Merge CBOR objects | `cbor1.merge(cbor2)` |

### Nested Access

```python
# Get nested
cbor.get_nested("user.address.city")
cbor.get_nested("items.0.name")
cbor.get_nested("missing.path", default="N/A")

# Set nested (auto-creates intermediate dicts)
cbor.set_nested("user.profile.name", "Alice")
cbor.set_nested("config.server.port", 8080)
```

### Dict-like Interface

```python
cbor["key"] = "value"        # Set
value = cbor["key"]          # Get
del cbor["key"]              # Delete
"key" in cbor                # Check
len(cbor)                    # Length
for k in cbor: ...           # Iterate
cbor.get("key", default)     # Safe get
cbor.keys()                  # Keys
cbor.values()                # Values
cbor.items()                 # Items
```

### Encoding

```python
cbor_bytes = cbor.encode()              # Standard
cbor_bytes = cbor.encode(canonical=True) # Canonical
```

### Complete Example

```python
# Build and encode in one go
cbor_bytes = (CBOR({})
              .set("id", "001")
              .set("name", "Alice")
              .set("tags", [])
              .update(active=True, verified=False)
              .encode())

# Or build step-by-step
cbor = CBOR({})
cbor.set("users", [])
cbor["users"].append({"name": "Alice", "role": "admin"})
cbor["users"].append({"name": "Bob", "role": "user"})
cbor.set("count", len(cbor["users"]))
cbor_bytes = cbor.encode()
```

### Common Patterns

**Initialize with defaults:**
```python
cbor = (CBOR({})
        .set("version", "1.0")
        .set("created_at", timestamp())
        .set("data", {}))
```

**Build arrays:**
```python
cbor = CBOR([])
for item in items:
    cbor.append(process(item))
```

**Modify and re-encode:**
```python
cbor = CBOR.load(bytes)
cbor.set("updated", True)
cbor["version"] += 1
new_bytes = cbor.encode()
```

**Deep nesting:**
```python
cbor = CBOR({})
cbor.set_nested("a.b.c.d.e", "deep value")
# Creates: {"a": {"b": {"c": {"d": {"e": "deep value"}}}}}
```

---

## Method Chaining Examples

```python
# Request builder
request = (CBOR({})
           .set("method", "POST")
           .set("endpoint", "/api/users")
           .set("body", {})
           .set("headers", {}))

request["body"]["username"] = "alice"
request["headers"]["Content-Type"] = "application/cbor"

# Sensor data
reading = (CBOR({})
           .set("sensor_id", "temp-01")
           .set("timestamp", time.time())
           .set("value", 23.5)
           .set("unit", "celsius"))

# Configuration
config = (CBOR({})
          .set("server", {})
          .set("database", {})
          .set("logging", {}))

config["server"].update({"host": "localhost", "port": 8080})
config["database"].update({"url": "postgres://...", "pool_size": 10})
```

---

## All Available Methods

### Construction
- `CBOR(data)` - Construct a CBOR instance from a Python object
- `CBOR.load(bytes)` - Parse CBOR bytes and return a CBOR instance
- `CBOR.loads(bytes)` - Decode CBOR bytes directly to the Python object (returns the decoded Python object, not a CBOR instance)

### Builder Pattern (returns self)
- `.set(key, value)` - Set single key-value
- `.update(dict, **kwargs)` - Update multiple keys
- `.delete(key)` - Delete key
- `.append(value)` - Append to array
- `.extend(values)` - Extend array
- `.clear()` - Clear all data
- `.merge(other_cbor)` - Merge another CBOR

### Nested Access
- `.get_nested(path, separator=".", default=None)` - Get nested value
- `.set_nested(path, value, separator=".", create=True)` - Set nested value

### Dict/List Interface
- `[key]` - Get/set item
- `del [key]` - Delete item
- `key in cbor` - Check membership
- `len(cbor)` - Get length
- `iter(cbor)` - Iterate
- `.get(key, default)` - Safe get
- `.keys()` - Get keys
- `.values()` - Get values
- `.items()` - Get items

### Utilities
- `.copy()` - Deep copy
- `.to_dict()` - Get as dict (if data is dict)
- `.to_list()` - Get as list (if data is list)
- `.encode(canonical=False)` - Encode to CBOR bytes
- `.diag(indent="  ")` - Get diagnostic dump

### Properties
- `.data` - The underlying Python object

---

## Tips

1. ✅ **Chain methods** for concise code
2. ✅ **Use nested paths** for deep structures
3. ✅ **Cache encoded bytes** if encoding multiple times
4. ✅ **Use copy()** to create variants
5. ✅ **Use update()** for multiple fields at once
6. ✅ **Use extend()** instead of multiple append()
7. ✅ **Direct access (.data)** still works for complex operations

---

## Test Coverage

✅ **26 tests** covering:
- Builder pattern (7 tests)
- Nested access (5 tests)
- Utility methods (6 tests)
- Iterative construction (4 tests)
- Complex scenarios (4 tests)

All tests passing! (100%)
