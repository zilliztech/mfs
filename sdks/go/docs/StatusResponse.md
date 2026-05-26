# StatusResponse

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Connectors** | [**[]ConnectorRow**](ConnectorRow.md) |  | 
**Jobs** | Pointer to **map[string]int32** | count of jobs by status | [optional] 

## Methods

### NewStatusResponse

`func NewStatusResponse(connectors []ConnectorRow, ) *StatusResponse`

NewStatusResponse instantiates a new StatusResponse object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewStatusResponseWithDefaults

`func NewStatusResponseWithDefaults() *StatusResponse`

NewStatusResponseWithDefaults instantiates a new StatusResponse object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetConnectors

`func (o *StatusResponse) GetConnectors() []ConnectorRow`

GetConnectors returns the Connectors field if non-nil, zero value otherwise.

### GetConnectorsOk

`func (o *StatusResponse) GetConnectorsOk() (*[]ConnectorRow, bool)`

GetConnectorsOk returns a tuple with the Connectors field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetConnectors

`func (o *StatusResponse) SetConnectors(v []ConnectorRow)`

SetConnectors sets Connectors field to given value.


### GetJobs

`func (o *StatusResponse) GetJobs() map[string]int32`

GetJobs returns the Jobs field if non-nil, zero value otherwise.

### GetJobsOk

`func (o *StatusResponse) GetJobsOk() (*map[string]int32, bool)`

GetJobsOk returns a tuple with the Jobs field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetJobs

`func (o *StatusResponse) SetJobs(v map[string]int32)`

SetJobs sets Jobs field to given value.

### HasJobs

`func (o *StatusResponse) HasJobs() bool`

HasJobs returns a boolean if a field has been set.


[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


