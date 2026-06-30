# BrowseApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**_export**](BrowseApi.md#_export) | **GET** /v1/export | Export |
| [**cat**](BrowseApi.md#cat) | **GET** /v1/cat | Cat |
| [**head**](BrowseApi.md#head) | **GET** /v1/head | Head |
| [**ls**](BrowseApi.md#ls) | **GET** /v1/ls | Ls |
| [**tail**](BrowseApi.md#tail) | **GET** /v1/tail | Tail |



## _export

> CatResponse _export(path)

Export

Full object content for &#x60;mfs export&#x60;. Honest about completeness: each connector\&#39;s own row cap still applies (postgres &#x60;max_read_rows&#x60;, BigQuery &#x60;max_read_rows&#x60;, etc.), so structured objects above that threshold return &#x60;partial&#x3D;true&#x60;. The bare-cat size guard (object_too_large_for_cat) does NOT apply — export is the escape hatch for that — but true streaming export is still TODO.

### Example

```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { ExportRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new BrowseApi(config);

  const body = {
    // string
    path: path_example,
  } satisfies ExportRequest;

  try {
    const data = await api._export(body);
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

[**CatResponse**](CatResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## cat

> CatResponse cat(path, range, meta, density, locator)

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
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new BrowseApi(config);

  const body = {
    // string
    path: path_example,
    // string (optional)
    range: range_example,
    // boolean (optional)
    meta: true,
    // string (optional)
    density: density_example,
    // string (optional)
    locator: locator_example,
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
| **locator** | `string` |  | [Optional] [Defaults to `undefined`] |

### Return type

[**CatResponse**](CatResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## head

> CatResponse head(path, n)

Head

### Example

```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { HeadRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new BrowseApi(config);

  const body = {
    // string
    path: path_example,
    // number (optional)
    n: 56,
  } satisfies HeadRequest;

  try {
    const data = await api.head(body);
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
| **n** | `number` |  | [Optional] [Defaults to `20`] |

### Return type

[**CatResponse**](CatResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

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
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new BrowseApi(config);

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

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## tail

> CatResponse tail(path, n)

Tail

### Example

```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { TailRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new BrowseApi(config);

  const body = {
    // string
    path: path_example,
    // number (optional)
    n: 56,
  } satisfies TailRequest;

  try {
    const data = await api.tail(body);
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
| **n** | `number` |  | [Optional] [Defaults to `20`] |

### Return type

[**CatResponse**](CatResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

