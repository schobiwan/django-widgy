from django.db import models
from django import forms
from django.utils.datastructures import SortedDict
from django.core.urlresolvers import reverse
from django.core.mail import send_mail
from django.conf import settings
from django.db.models.query import QuerySet

from fusionbox import behaviors
from fusionbox.db.models import QuerySetManager
from django_extensions.db.fields import UUIDField

from widgy.models import Content, Node
from widgy.models.mixins import DefaultChildrenMixin
from widgy.utils import update_context
from widgy.contrib.page_builder.db.fields import MarkdownField
import widgy


class FormElement(Content):
    editable = True

    class Meta:
        abstract = True

    @property
    def parent_form(self):
        for i in self.get_ancestors():
            if isinstance(i, Form):
                return i

        assert False, "This FormElement, doesn't belong to a Form?!?!?"

    @classmethod
    def valid_child_of(cls, parent, obj=None):
        for p in list(parent.get_ancestors()) + [parent]:
            if isinstance(p, Form):
                return super(FormElement, cls).valid_child_of(parent, obj)
        return False


class FormSuccessHandler(FormElement):
    draggable = False

    class Meta:
        abstract = True

    @classmethod
    def valid_child_of(cls, parent, obj=None):
        return isinstance(parent, SubmitButton)


class FormReponseHandler(FormSuccessHandler):
    class Meta:
        abstract = True


@widgy.register
class EmailSuccessHandler(FormSuccessHandler):
    to = models.EmailField()
    content = MarkdownField(blank=True)

    component_name = 'markdown'

    def execute(self, request, form):
        send_mail('Subject', self.content, settings.SERVER_EMAIL, [self.to])


@widgy.register
class SaveDataHandler(FormSuccessHandler):
    editable = False

    def execute(self, request, form):
        FormSubmission.objects.submit(
            form=self.parent_form,
            data=form.cleaned_data
        )


@widgy.register
class SubmitButton(DefaultChildrenMixin, FormElement):
    text = models.CharField(max_length=255, default='submit')

    default_children = [
        (SaveDataHandler, (), {}),
    ]

    @property
    def deletable(self):
        return len([i for i in self.parent_form.depth_first_order() if isinstance(i, SubmitButton)]) > 1

    def valid_parent_of(self, cls, obj=None):
        if obj in self.get_children():
            return True

        # only accept one FormReponseHandler
        if issubclass(cls, FormReponseHandler) and any([isinstance(child, FormReponseHandler)
                                                        for child in self.get_children()]):
            return False

        return issubclass(cls, FormSuccessHandler)


def untitled_form():
    n = Form.objects.filter(name__startswith='Untitled form ').exclude(
        _nodes__is_frozen=True
    ).count() + 1
    return 'Untitled form %d' % n


@widgy.register
class Form(DefaultChildrenMixin, Content):
    name = models.CharField(max_length=255,
                            default=untitled_form,
                            help_text="A name to help identify this form. Only admins see this.")
    ident = UUIDField()
    # This gets annotate()d on, but has to be set to something first for django
    # to let us put it in the list_display.
    submission_count = None


    accepting_children = True
    shelf = True
    editable = True

    default_children = [
        (SubmitButton, (), {}),
    ]

    objects = QuerySetManager()

    class QuerySet(QuerySet):
        def annotate_submission_count(self):
            return self.extra(select={
                'submission_count':
                'SELECT COUNT(*) FROM form_builder_formsubmission'
                ' WHERE form_ident = form_builder_form.ident'
            })

    def __unicode__(self):
        return self.name

    @property
    def action_url(self):
        return reverse('widgy.contrib.widgy_mezzanine.views.handle_form',
                       kwargs={
                           'node_pk': self.node.pk,
                       })

    def valid_parent_of(self, cls, obj=None):
        return True

    @classmethod
    def valid_child_of(cls, parent, obj=None):
        for p in list(parent.get_ancestors()) + [parent]:
            if isinstance(p, Form):
                return False
        return super(Form, cls).valid_child_of(parent, obj)

    def get_form(self):
        fields = SortedDict((child.get_formfield_name(), child.get_formfield())
                            for child in self.get_children() if isinstance(child, FormField))

        return type('WidgyForm', (forms.BaseForm,), {'base_fields': fields})

    @property
    def context_var(self):
        return 'form_instance_{node_pk}'.format(node_pk=self.node.pk)

    def render(self, context):
        if self.context_var in context:
            form = context[self.context_var]
        else:
            form = self.get_form()()

        with update_context(context, {'form': form}):
            return super(Form, self).render(context)

    def execute(self, request, form):
        # TODO: only call the handlers for the submit button that was pressed.
        resp = None
        for child in self.depth_first_order():
            if isinstance(child, FormReponseHandler):
                resp = child.execute(request, form)
            elif isinstance(child, FormSuccessHandler):
                child.execute(request, form)
        return resp

    def make_root(self):
        """
        Turns us into a root node by taking us out of the tree we're in.
        """
        self.node.move(Node.get_last_root_node(),
                       'last-sibling')

    def delete(self):
        self.check_frozen()
        # don't delete, just take us of the the tree
        self.make_root()

    def get_fields(self):
        ret = {}
        for child in self.depth_first_order():
            if isinstance(child, FormField):
                ret[child.get_formfield_name()] = child
        return ret

    @property
    def submissions(self):
        return FormSubmission.objects.filter(
            form_ident=self.ident
        ).prefetch_related('values')

    @property
    def submission_count(self):
        if hasattr(self, '_submission_count'):
            return self._submission_count

        return self.submissions.count()

    @submission_count.setter
    def submission_count(self, value):
        self._submission_count = value

    @models.permalink
    def submission_url(self):
        return ('admin:%s_%s_change' % (self._meta.app_label, self._meta.module_name),
                (self.pk,),
                {})


class FormField(FormElement):
    formfield_class = None
    widget = None

    label = models.CharField(max_length=255)

    help_text = models.TextField(blank=True)
    ident = UUIDField()

    class Meta:
        abstract = True

    def get_formfield_name(self):
        return str(self.node.pk)

    def get_formfield(self):
        kwargs = {
            'label': self.label,
            'help_text': self.help_text,
            'widget': self.widget,
        }

        return self.formfield_class(**kwargs)

    def render(self, context):
        form = context['form']
        field = form[self.get_formfield_name()]
        with update_context(context, {'field': field}):
            return super(FormField, self).render(context)


FORM_INPUT_TYPES = (
    ('text', 'Text'),
    ('number', 'Number'),
)

class FormInputForm(forms.ModelForm):
    class Meta:
        fields = (
            'type',
            'label',
            'help_text',
        )


@widgy.register
class FormInput(FormField):
    formfield_class = forms.CharField
    form = FormInputForm

    type = models.CharField(choices=FORM_INPUT_TYPES, max_length=255)


@widgy.register
class Textarea(FormField):
    formfield_class = forms.CharField
    widget = forms.Textarea


class FormSubmission(behaviors.Timestampable, models.Model):
    form_node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name='form_submissions')
    form_ident = models.CharField(max_length=Form._meta.get_field_by_name('ident')[0].max_length)

    objects = QuerySetManager()

    class QuerySet(QuerySet):
        def field_names(self):
            """
            A dictionary of field uuid to field label. We used the label of the
            field that was used by the most recent submission. Note that this
            means only fields that have been submitted will show up here.
            """

            uuids = FormValue.objects.filter(
                submission__in=self,
            ).values('field_ident').distinct().values_list('field_ident', flat=True)

            ret = {}
            for field_uuid in uuids:
                latest_value = FormValue.objects.filter(
                    field_ident=field_uuid,
                ).order_by('-submission__created_at', '-pk').select_related('field_node')[0]
                if latest_value.field_node:
                    name = latest_value.field_node.content.label
                else:
                    name = latest_value.field_name
                ret[field_uuid] = name
            return ret

        def as_dictionaries(self):
            return (i.as_dict() for i in self.all())

        def submit(self, form, data):
            submission = self.create(
                form_node=form.node,
                form_ident=form.ident,
            )

            for name, field in form.get_fields().iteritems():
                submission.values.create(
                    field_node=field.node,
                    field_name=field.label,
                    field_ident=field.ident,
                    value=data[name]
                )
            return submission

    def as_dict(self):
        ret = {}
        for value in self.values.all():
            ret[value.field_ident] = value.value
        return ret


class FormValue(models.Model):
    submission = models.ForeignKey(FormSubmission, related_name='values')
    field_node = models.ForeignKey(Node, on_delete=models.SET_NULL, null=True)
    field_name = models.CharField(max_length=255)
    field_ident = models.CharField(
        max_length=FormField._meta.get_field_by_name('ident')[0].max_length)
    value = models.TextField()
