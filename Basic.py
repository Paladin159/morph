from morphcloud.api import MorphCloudClient

# Initialize the client
client = MorphCloudClient()

# Create a snapshot with minimal resources
snapshot = client.snapshots.create(
    vcpus=1, 
    memory=128, 
    disk_size=700, 
    image_id="morphvm-minimal"
)

# Start an instance from the snapshot
with client.instances.start(snapshot_id=snapshot.id) as instance:
    # Connect via SSH
    with instance.ssh() as ssh:
        # Run a simple command
        result = ssh.run(["echo", "Hello, World!"])
        print(result.stdout)
