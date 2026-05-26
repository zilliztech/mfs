# ServerInfo

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Version** | **string** | server semver, e.g. 0.4.0 | 
**MachineId** | **string** | host identifier | 
**Namespace** | **string** | active namespace | 

## Methods

### NewServerInfo

`func NewServerInfo(version string, machineId string, namespace string, ) *ServerInfo`

NewServerInfo instantiates a new ServerInfo object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewServerInfoWithDefaults

`func NewServerInfoWithDefaults() *ServerInfo`

NewServerInfoWithDefaults instantiates a new ServerInfo object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetVersion

`func (o *ServerInfo) GetVersion() string`

GetVersion returns the Version field if non-nil, zero value otherwise.

### GetVersionOk

`func (o *ServerInfo) GetVersionOk() (*string, bool)`

GetVersionOk returns a tuple with the Version field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetVersion

`func (o *ServerInfo) SetVersion(v string)`

SetVersion sets Version field to given value.


### GetMachineId

`func (o *ServerInfo) GetMachineId() string`

GetMachineId returns the MachineId field if non-nil, zero value otherwise.

### GetMachineIdOk

`func (o *ServerInfo) GetMachineIdOk() (*string, bool)`

GetMachineIdOk returns a tuple with the MachineId field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetMachineId

`func (o *ServerInfo) SetMachineId(v string)`

SetMachineId sets MachineId field to given value.


### GetNamespace

`func (o *ServerInfo) GetNamespace() string`

GetNamespace returns the Namespace field if non-nil, zero value otherwise.

### GetNamespaceOk

`func (o *ServerInfo) GetNamespaceOk() (*string, bool)`

GetNamespaceOk returns a tuple with the Namespace field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetNamespace

`func (o *ServerInfo) SetNamespace(v string)`

SetNamespace sets Namespace field to given value.



[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


