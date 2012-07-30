from widgy.models import *


page = ContentPage.objects.create(
        title='widgy page'
        )
page.root_widget = TwoColumnLayout.add_root().node
page.save()

for i in range(3):
    page.root_widget.data.left_bucket.data.add_child(TextContent,
            content='yay %s' % i
            )

for i in range(2):
    page.root_widget.data.right_bucket.data.add_child(TextContent,
            content='yay right bucket %s' % i
            )