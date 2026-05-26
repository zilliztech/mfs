# RetrievalApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**grep**](RetrievalApi.md#grep) | **GET** /v1/grep | Grep |
| [**search**](RetrievalApi.md#search) | **GET** /v1/search | Search |



## grep

> GrepResponse grep(pattern, path)

Grep

### Example

```ts
import {
  Configuration,
  RetrievalApi,
} from '@mfs/sdk';
import type { GrepRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new RetrievalApi();

  const body = {
    // string
    pattern: pattern_example,
    // string
    path: path_example,
  } satisfies GrepRequest;

  try {
    const data = await api.grep(body);
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
| **pattern** | `string` |  | [Defaults to `undefined`] |
| **path** | `string` |  | [Defaults to `undefined`] |

### Return type

[**GrepResponse**](GrepResponse.md)

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


## search

> SearchResponse search(q, path, mode, topK, collapse)

Search

### Example

```ts
import {
  Configuration,
  RetrievalApi,
} from '@mfs/sdk';
import type { SearchRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new RetrievalApi();

  const body = {
    // string
    q: q_example,
    // string (optional)
    path: path_example,
    // string (optional)
    mode: mode_example,
    // number (optional)
    topK: 56,
    // boolean (optional)
    collapse: true,
  } satisfies SearchRequest;

  try {
    const data = await api.search(body);
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
| **q** | `string` |  | [Defaults to `undefined`] |
| **path** | `string` |  | [Optional] [Defaults to `undefined`] |
| **mode** | `string` |  | [Optional] [Defaults to `&#39;hybrid&#39;`] |
| **topK** | `number` |  | [Optional] [Defaults to `10`] |
| **collapse** | `boolean` |  | [Optional] [Defaults to `false`] |

### Return type

[**SearchResponse**](SearchResponse.md)

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

