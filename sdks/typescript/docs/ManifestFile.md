
# ManifestFile


## Properties

Name | Type
------------ | -------------
`path` | string
`size` | number
`mtimeNs` | number
`inode` | number

## Example

```typescript
import type { ManifestFile } from '@mfs/sdk'

// TODO: Update the object below with actual values
const example = {
  "path": null,
  "size": null,
  "mtimeNs": null,
  "inode": null,
} satisfies ManifestFile

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ManifestFile
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


