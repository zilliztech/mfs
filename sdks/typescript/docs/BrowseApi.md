# BrowseApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**cat**](BrowseApi.md#cat) | **GET** /v1/cat | Cat |
| [**ls**](BrowseApi.md#ls) | **GET** /v1/ls | Ls |



## cat

> CatResponse cat(path, range, meta, density)

Cat

### Example

```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { CatRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new BrowseApi();

  const body = {
    // string
    path: path_example,
    // string (optional)
    range: range_example,
    // boolean (optional)
    meta: true,
    // string (optional)
    density: density_example,
  } satisfies CatRequest;

  try {
    const data = await api.cat(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **path** | `string` |  | [Defaults to `undefined`] |
| **range** | `string` |  | [Optional] [Defaults to `undefined`] |
| **meta** | `boolean` |  | [Optional] [Defaults to `false`] |
| **density** | `string` |  | [Optional] [Defaults to `undefined`] |

### Return type

[**CatResponse**](CatResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## ls

> LsResponse ls(path)

Ls

### Example

```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { LsRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new BrowseApi();

  const body = {
    // string
    path: path_example,
  } satisfies LsRequest;

  try {
    const data = await api.ls(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **path** | `string` |  | [Defaults to `undefined`] |

### Return type

[**LsResponse**](LsResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

