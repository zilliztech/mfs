# ConnectorsApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**estimateConnector**](ConnectorsApi.md#estimateconnector) | **POST** /v1/connectors/estimate | Estimate |
| [**inspectConnector**](ConnectorsApi.md#inspectconnector) | **GET** /v1/connectors/inspect | Inspect |
| [**probeConnector**](ConnectorsApi.md#probeconnector) | **POST** /v1/connectors/probe | Probe |
| [**removeConnector**](ConnectorsApi.md#removeconnector) | **DELETE** /v1/connectors | Remove |



## estimateConnector

> EstimateResponse estimateConnector(probeRequest)

Estimate

Zero-billing pre-flight estimate: object/chunk/token counts via metadata + a local chunker/tokenizer dry-run. No embedding API calls.

### Example

```ts
import {
  Configuration,
  ConnectorsApi,
} from '@mfs/sdk';
import type { EstimateConnectorRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new ConnectorsApi(config);

  const body = {
    // ProbeRequest
    probeRequest: ...,
  } satisfies EstimateConnectorRequest;

  try {
    const data = await api.estimateConnector(body);
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
| **probeRequest** | [ProbeRequest](ProbeRequest.md) |  | |

### Return type

[**EstimateResponse**](EstimateResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: `application/json`
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


## inspectConnector

> any inspectConnector(target)

Inspect

### Example

```ts
import {
  Configuration,
  ConnectorsApi,
} from '@mfs/sdk';
import type { InspectConnectorRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new ConnectorsApi(config);

  const body = {
    // string
    target: target_example,
  } satisfies InspectConnectorRequest;

  try {
    const data = await api.inspectConnector(body);
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
| **target** | `string` |  | [Defaults to `undefined`] |

### Return type

**any**

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


## probeConnector

> ProbeResponse probeConnector(probeRequest)

Probe

### Example

```ts
import {
  Configuration,
  ConnectorsApi,
} from '@mfs/sdk';
import type { ProbeConnectorRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new ConnectorsApi(config);

  const body = {
    // ProbeRequest
    probeRequest: ...,
  } satisfies ProbeConnectorRequest;

  try {
    const data = await api.probeConnector(body);
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
| **probeRequest** | [ProbeRequest](ProbeRequest.md) |  | |

### Return type

[**ProbeResponse**](ProbeResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: `application/json`
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


## removeConnector

> RemoveResponse removeConnector(target)

Remove

### Example

```ts
import {
  Configuration,
  ConnectorsApi,
} from '@mfs/sdk';
import type { RemoveConnectorRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new ConnectorsApi(config);

  const body = {
    // string
    target: target_example,
  } satisfies RemoveConnectorRequest;

  try {
    const data = await api.removeConnector(body);
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
| **target** | `string` |  | [Defaults to `undefined`] |

### Return type

[**RemoveResponse**](RemoveResponse.md)

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

