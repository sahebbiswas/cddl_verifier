# CBOR Iterative Construction & Modification Guide

## Overview

The CBOR class now provides a **complete fluent API** for iterative construction and modification of CBOR data structures. You can build complex CBOR objects step-by-step using intuitive, Pythonic methods.

**Key Features:**
- ✅ **Builder Pattern** - Chain methods for fluent construction
- ✅ **Nested Path Access** - Set/get values using dot notation
- ✅ **Auto Re-encoding** - Changes automatically invalidate cache
- ✅ **Dict/List Interface** - Works like native Python objects
- ✅ **Type Safety** - Appropriate errors for wrong operations
- ✅ **Zero Copying** - Modify in-place for efficiency

---

## Quick Start

### Basic Construction

```python
from simple_cbor import CBOR

# Start with empty dict and build iteratively
cbor = CBOR({})
cbor.set("name", "Alice")
cbor.set("age", 30)
cbor.set("tags", ["admin", "user"])

print(cbor.data)
# Output: {'name': 'Alice', 'age': 30, 'tags': ['admin', 'user']}

# Encode when ready
cbor_bytes = cbor.encode()
```

### Fluent API (Method Chaining)

```python
# Build complex structures in one expression
cbor = (CBOR({})
        .set("id", "user-001")
        .set("name", "Alice")
        .set("roles", [])
        .update({"active": True, "verified": True}))

# Add to nested arrays
cbor["roles"].extend(["admin", "developer"])

# Encode
cbor_bytes = cbor.encode()
```

---

## Builder Pattern Methods

### Dictionary Operations

#### `set(key, value)` - Set a single key-value pair

```python
cbor = CBOR({})
cbor.set("name", "Bob")
cbor.set("email", "bob@example.com")
cbor.set("age", 25)

# Returns self for chaining
cbor.set("a", 1).set("b", 2).set("c", 3)
```

#### `update(dict, **kwargs)` - Update with multiple values

```python
cbor = CBOR({"a": 1})

# Update from dict
cbor.update({"b": 2, "c": 3})

# Update with kwargs
cbor.update(d=4, e=5)

# Both at once
cbor.update({"f": 6}, g=7, h=8)

print(cbor.data)
# {'a': 1, 'b': 2, 'c': 3, 'd': 4, 'e': 5, 'f': 6, 'g': 7, 'h': 8}
```

#### `delete(key)` - Remove a key

```python
cbor = CBOR({"a": 1, "b": 2, "c": 3})
cbor.delete("b")

print(cbor.data)
# {'a': 1, 'c': 3}

# Chain deletions
cbor.delete("a").delete("c")
# {}
```

### Array Operations

#### `append(value)` - Add single item

```python
cbor = CBOR([])
cbor.append(1)
cbor.append(2)
cbor.append(3)

print(cbor.data)
# [1, 2, 3]

# Chain appends
cbor.append(4).append(5).append(6)
# [1, 2, 3, 4, 5, 6]
```

#### `extend(values)` - Add multiple items

```python
cbor = CBOR([1, 2, 3])
cbor.extend([4, 5, 6])

print(cbor.data)
# [1, 2, 3, 4, 5, 6]

# Chain extends
cbor.extend([7, 8]).extend([9, 10])
```

### Utility Methods

#### `clear()` - Remove all data

```python
cbor = CBOR({"a": 1, "b": 2})
cbor.clear()
# {}

cbor_list = CBOR([1, 2, 3])
cbor_list.clear()
# []
```

#### `copy()` - Create deep copy

```python
cbor1 = CBOR({"a": 1, "nested": {"b": 2}})
cbor2 = cbor1.copy()

# Modify copy
cbor2.set("a", 999)
cbor2["nested"]["b"] = 888

# Original unchanged
print(cbor1.data)
# {'a': 1, 'nested': {'b': 2}}
```

#### `merge(other_cbor)` - Merge another CBOR object

```python
cbor1 = CBOR({"a": 1, "b": 2})
cbor2 = CBOR({"c": 3, "d": 4})

cbor1.merge(cbor2)
print(cbor1.data)
# {'a': 1, 'b': 2, 'c': 3, 'd': 4}

# Works with lists too
list1 = CBOR([1, 2, 3])
list2 = CBOR([4, 5, 6])
list1.merge(list2)
# [1, 2, 3, 4, 5, 6]
```

---

## Nested Path Access

### Get Nested Values

Use dot notation to access deeply nested structures:

```python
cbor = CBOR({
    "user": {
        "profile": {
            "name": "Alice",
            "address": {
                "city": "NYC",
                "zip": "10001"
            }
        }
    }
})

# Access with path
name = cbor.get_nested("user.profile.name")
# "Alice"

city = cbor.get_nested("user.profile.address.city")
# "NYC"

# With default for missing paths
age = cbor.get_nested("user.profile.age", default=0)
# 0
```

### Set Nested Values

Auto-create intermediate dictionaries:

```python
cbor = CBOR({})

# This creates the entire path automatically
cbor.set_nested("user.profile.address.city", "NYC")

print(cbor.data)
# {
#   'user': {
#     'profile': {
#       'address': {
#         'city': 'NYC'
#       }
#     }
#   }
# }

# Add more nested values
cbor.set_nested("user.profile.name", "Alice")
cbor.set_nested("user.profile.age", 30)
cbor.set_nested("user.settings.theme", "dark")
```

### Arrays in Paths

Access array elements by index:

```python
cbor = CBOR({
    "items": [
        {"name": "Item 1", "price": 10},
        {"name": "Item 2", "price": 20},
        {"name": "Item 3", "price": 30}
    ]
})

# Access by index
name = cbor.get_nested("items.0.name")
# "Item 1"

price = cbor.get_nested("items.1.price")
# 20
```

---

## Dict/List Interface

The CBOR object behaves like a native Python dict or list:

### Dictionary-like Access

```python
cbor = CBOR({"name": "Alice", "age": 30})

# Get items
print(cbor["name"])  # "Alice"
print(cbor.get("age"))  # 30
print(cbor.get("city", "Unknown"))  # "Unknown"

# Set items
cbor["email"] = "alice@example.com"

# Delete items
del cbor["age"]

# Check membership
if "name" in cbor:
    print("Has name")

# Iterate
for key in cbor:
    print(key, cbor[key])

# Keys, values, items
print(list(cbor.keys()))    # ['name', 'email']
print(list(cbor.values()))  # ['Alice', 'alice@example.com']
print(list(cbor.items()))   # [('name', 'Alice'), ...]
```

### List-like Access

```python
cbor = CBOR([1, 2, 3, 4, 5])

# Get items
print(cbor[0])  # 1
print(cbor[-1])  # 5

# Set items
cbor[2] = 99

# Length
print(len(cbor))  # 5

# Iterate
for item in cbor:
    print(item)

# Append (Python list method)
cbor.data.append(6)

# Or use fluent method
cbor.append(7)
```

---

## Complete Examples

### Example 1: Building a CoRIM Structure

```python
from simple_cbor import CBOR

# Create CoRIM structure iteratively
corim = CBOR({})

# Set basic fields
corim.set("id", "example.corim.001")
corim.set("tags", [])

# Build first tag
tag1 = {
    "id": "tag-001",
    "environment": {
        "class": {"id": 1},
        "instance": b'\x01\x02\x03\x04'
    }
}

# Build second tag
tag2 = {
    "id": "tag-002",
    "environment": {
        "class": {"id": 2},
        "instance": b'\x05\x06\x07\x08'
    }
}

# Add tags
corim["tags"].append(tag1)
corim["tags"].append(tag2)

# Wrap in tag 501 (unsigned-corim-map)
tagged_corim = (501, corim.data)

# Encode
cbor_bytes = CBOR(tagged_corim).encode(canonical=True)
print(f"Encoded {len(cbor_bytes)} bytes")
```

### Example 2: API Request Builder

```python
# Build API request payload
request = (CBOR({})
           .set("jsonrpc", "2.0")
           .set("method", "user.create")
           .set("id", 1)
           .set("params", {}))

# Add parameters
request["params"]["username"] = "alice"
request["params"]["email"] = "alice@example.com"
request["params"]["roles"] = ["admin", "developer"]
request["params"]["metadata"] = {
    "created_by": "system",
    "source": "api"
}

# Encode for transmission
request_bytes = request.encode()

# Send over network...
send_to_server(request_bytes)
```

### Example 3: Sensor Data Accumulation

```python
# Initialize sensor log
sensor_log = CBOR({
    "sensor_id": "temp-sensor-001",
    "location": "Room A",
    "readings": []
})

# Simulate collecting readings over time
import time

for i in range(10):
    reading = {
        "timestamp": int(time.time()),
        "temperature": 20.0 + (i * 0.5),
        "humidity": 45.0 + (i * 2)
    }
    sensor_log["readings"].append(reading)
    time.sleep(0.1)

# Add summary statistics
readings = sensor_log["readings"]
sensor_log.set("count", len(readings))
sensor_log.set("avg_temp", sum(r["temperature"] for r in readings) / len(readings))
sensor_log.set("avg_humidity", sum(r["humidity"] for r in readings) / len(readings))

# Encode and save
cbor_bytes = sensor_log.encode()
with open("sensor_log.cbor", "wb") as f:
    f.write(cbor_bytes)
```

### Example 4: Incremental Document Builder

```python
# Build a document incrementally
doc = CBOR({})

# Add metadata
doc.set_nested("metadata.title", "My Document")
doc.set_nested("metadata.author", "Alice")
doc.set_nested("metadata.created", "2024-01-01")
doc.set_nested("metadata.version", 1)

# Add content sections
doc.set("sections", [])

# Add sections one by one
section1 = {
    "title": "Introduction",
    "content": "This is the introduction...",
    "order": 1
}

section2 = {
    "title": "Main Content",
    "content": "This is the main content...",
    "order": 2
}

doc["sections"].append(section1)
doc["sections"].append(section2)

# Add tags
doc.set("tags", ["important", "draft"])

# Add more tags later
doc["tags"].extend(["reviewed", "final"])

# Encode
cbor_bytes = doc.encode()
```

### Example 5: Modify Existing CBOR

```python
# Load existing CBOR
with open("data.cbor", "rb") as f:
    cbor_bytes = f.read()

# Load into CBOR object
cbor = CBOR.load(cbor_bytes)

# Modify
cbor.set("last_modified", "2024-01-15")
cbor.set("version", cbor.get("version", 1) + 1)

# Add new field
cbor.set("reviewed", True)

# Modify nested structure
if "metadata" in cbor:
    cbor["metadata"]["updated"] = True

# Re-encode and save
updated_bytes = cbor.encode()
with open("data.cbor", "wb") as f:
    f.write(updated_bytes)
```

---

## Advanced Patterns

### Conditional Building

```python
def build_user_profile(name, email, admin=False, verified=False):
    """Build user profile based on conditions"""
    cbor = (CBOR({})
            .set("name", name)
            .set("email", email))
    
    # Conditional fields
    if admin:
        cbor.set("roles", ["admin", "user"])
        cbor.set("permissions", ["read", "write", "delete"])
    else:
        cbor.set("roles", ["user"])
        cbor.set("permissions", ["read"])
    
    if verified:
        cbor.set("verified", True)
        cbor.set("verified_at", "2024-01-01")
    
    return cbor.encode()
```

### Builder Class Pattern

```python
class CoRIMBuilder:
    """Fluent builder for CoRIM structures"""
    
    def __init__(self, corim_id):
        self.cbor = CBOR({})
        self.cbor.set("id", corim_id)
        self.cbor.set("tags", [])
    
    def add_tag(self, tag_id, environment):
        """Add a tag to the CoRIM"""
        tag = {
            "id": tag_id,
            "environment": environment
        }
        self.cbor["tags"].append(tag)
        return self
    
    def set_validity(self, not_before, not_after):
        """Set validity period"""
        self.cbor.set_nested("validity.not-before", not_before)
        self.cbor.set_nested("validity.not-after", not_after)
        return self
    
    def build(self, canonical=True):
        """Build the final CBOR"""
        return self.cbor.encode(canonical=canonical)

# Usage
corim_bytes = (CoRIMBuilder("corim-001")
               .add_tag("tag-1", {"class": {"id": 1}})
               .add_tag("tag-2", {"class": {"id": 2}})
               .set_validity("2024-01-01", "2025-01-01")
               .build())
```

### Template Pattern

```python
def create_from_template(template_cbor, **overrides):
    """Create new CBOR from template with overrides"""
    # Copy template
    cbor = template_cbor.copy()
    
    # Apply overrides
    for key, value in overrides.items():
        if "." in key:  # Nested path
            cbor.set_nested(key, value)
        else:
            cbor.set(key, value)
    
    return cbor

# Create template
template = CBOR({
    "type": "sensor_reading",
    "location": "default",
    "reading": 0,
    "metadata": {
        "version": 1,
        "format": "cbor"
    }
})

# Create instances from template
reading1 = create_from_template(
    template,
    location="Room A",
    reading=25.5,
    **{"metadata.timestamp": "2024-01-01"}
)

reading2 = create_from_template(
    template,
    location="Room B",
    reading=23.2,
    **{"metadata.timestamp": "2024-01-02"}
)
```

---

## Best Practices

### 1. Use Method Chaining for Construction

**Good:**
```python
cbor = (CBOR({})
        .set("id", "001")
        .set("name", "Alice")
        .set("active", True))
```

**Less Ideal:**
```python
cbor = CBOR({})
cbor.set("id", "001")
cbor.set("name", "Alice")
cbor.set("active", True)
```

### 2. Use Nested Paths for Deep Structures

**Good:**
```python
cbor = CBOR({})
cbor.set_nested("user.profile.name", "Alice")
cbor.set_nested("user.profile.email", "alice@example.com")
```

**Less Ideal:**
```python
cbor = CBOR({})
cbor["user"] = {}
cbor["user"]["profile"] = {}
cbor["user"]["profile"]["name"] = "Alice"
cbor["user"]["profile"]["email"] = "alice@example.com"
```

### 3. Cache Encoding Results

```python
# If encoding multiple times, cache the result
cbor = build_complex_structure()
cbor_bytes = cbor.encode()  # Cache this

# Use cached bytes multiple times
send_to_server(cbor_bytes)
save_to_file(cbor_bytes)
compute_hash(cbor_bytes)
```

### 4. Use Copy for Variants

```python
# Create base template
base = CBOR({"type": "user", "active": True})

# Create variants
admin = base.copy().set("role", "admin")
user = base.copy().set("role", "user")
guest = base.copy().set("role", "guest").set("active", False)
```

### 5. Validate Before Encoding

```python
def validate_and_encode(cbor):
    """Validate structure before encoding"""
    # Check required fields
    if "id" not in cbor:
        raise ValueError("Missing required field: id")
    if "name" not in cbor:
        raise ValueError("Missing required field: name")
    
    # Encode
    return cbor.encode(canonical=True)
```

---

## Performance Tips

1. **Modify in place** - Avoid creating intermediate copies
2. **Use extend() over multiple append()** - More efficient for bulk adds
3. **Cache encoded bytes** - Don't re-encode unnecessarily
4. **Use canonical only when needed** - Standard encoding is faster

---

## Error Handling

```python
# Type errors
cbor = CBOR([])
try:
    cbor.set("key", "value")  # Error: can't set on list
except TypeError as e:
    print(f"Error: {e}")

# Missing keys
cbor = CBOR({})
try:
    value = cbor.get_nested("missing.path.here", default="N/A")
    # Returns default, no error
except Exception as e:
    print(f"Error: {e}")

# Index errors
cbor = CBOR([1, 2, 3])
value = cbor.get(10, default=None)  # Returns None
```

---

## Migration from Direct Manipulation

### Before (Direct Manipulation)

```python
data = {}
data["name"] = "Alice"
data["age"] = 30
data["tags"] = []
data["tags"].append("admin")
cbor_bytes = cbor_encode(data)
```

### After (Fluent API)

```python
cbor = (CBOR({})
        .set("name", "Alice")
        .set("age", 30)
        .set("tags", []))

cbor["tags"].append("admin")  # Append to the tags list
cbor_bytes = cbor.encode()
```

Both work! The fluent API provides more expressiveness and better chaining.

---

## Summary

The CBOR class now supports:

✅ **Builder Pattern** - Chain methods fluently  
✅ **Iterative Construction** - Build step-by-step  
✅ **Nested Access** - Use dot notation for deep structures  
✅ **Full Dict/List API** - Works like Python natives  
✅ **Auto Re-encoding** - Changes invalidate cache automatically  
✅ **Copy and Merge** - Easy object manipulation  
✅ **Pythonic and Intuitive** - Feels natural to use  

**26 comprehensive tests** ensure all features work correctly!
