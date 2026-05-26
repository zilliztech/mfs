# ServerApi

All URIs are relative to *http://127.0.0.1:8765*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getServerInfo**](ServerApi.md#getserverinfo) | **GET** /v1/server/info | Server Info |
| [**status**](ServerApi.md#status) | **GET** /v1/status | Status |



## getServerInfo

> ServerInfo getServerInfo()

Server Info

### Example

```ts
import {
  Configuration,
  ServerApi,
} from '@mfs/sdk';
import type { GetServerInfoRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new ServerApi();

  try {
    const data = await api.getServerInfo();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**ServerInfo**](ServerInfo.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## status

> StatusResponse status()

Status

### Example

```ts
import {
  Configuration,
  ServerApi,
} from '@mfs/sdk';
import type { StatusRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const api = new ServerApi();

  try {
    const data = await api.status();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**StatusResponse**](StatusResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

